import { draw } from '/chart.js';
import { renderRows } from '/result-table.js';
import { versionPanel } from '/version-panel.js';

const $ = (id) => document.getElementById(id);
const base = document.body.dataset.prefix;
let model, packet, mode;
let busy = false;

async function request(path, data) {
  const options = data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  };
  const response = await fetch(base + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作未完成');
  return result;
}

function note(text, kind = '') {
  $('status').textContent = text;
  $('status').dataset.kind = kind;
}

const stageButtons = () => document.querySelectorAll('#stages button, #review-stages button, #plan-stages button');

function renderOperation(next) {
  mode = next;
  const spec = model.operations.find((op) => op.id === mode);
  $('table-title').textContent = spec.label;
  for (const button of stageButtons()) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  renderRows(model, packet.outputs[mode] ?? [], spec.columns);
}

function show(next) {
  packet = next;
  $('subject').textContent = next.subject;
  $('digest').textContent = next.digest.slice(0, 12);
  draw($('visual'), next, model);
  const review = Object.hasOwn(next.source, 'review');
  $('review-pane').hidden = !review;
  if (!review) $('review-pane').open = false;
  const plan = Object.hasOwn(next.source, 'plan');
  $('plan-pane').hidden = !plan;
  if (!plan) $('plan-pane').open = false;
  if (plan && !$('recheck-take').value.trim()) {
    $('recheck-take').value = JSON.stringify({
      id: '复验一', temperature: next.source.conditions.temperature, opened: next.source.opened, peaks: [],
    }, null, 2);
  }
  renderOperation(Object.hasOwn(next.outputs, mode) ? mode : model.defaultOperation);
}

function source() {
  try {
    return JSON.parse($('source').value);
  } catch {
    throw new Error('数据包不是有效的 JSON');
  }
}

async function guarded(action) {
  if (busy) return;
  busy = true;
  document.body.setAttribute('aria-busy', 'true');
  try {
    await action();
  } catch (error) {
    note(error.message === 'Failed to fetch' ? '无法连接本机服务' : error.message, 'error');
  } finally {
    busy = false;
    document.body.setAttribute('aria-busy', 'false');
  }
}

async function choose(path) {
  const data = await request(path);
  const next = await request('evaluate', data);
  $('source').value = JSON.stringify(data, null, 2);
  show(next);
  if (path === 'review-sample') {
    $('review-pane').open = true;
    renderOperation(model.operations.find((op) => op.optional && !op.group).id);
  }
  if (path === 'plan-sample') {
    $('plan-pane').open = true;
    renderOperation(model.operations.find((op) => op.group === 'plan').id);
  }
  note(({ sample:'样例已处理', alternate:'对照样例已处理', 'review-sample':'复核样例已处理', 'plan-sample':'作业样例已处理' })[path]);
}

function bind(id, action, event = 'click') {
  $(id).addEventListener(event, () => guarded(action));
}

guarded(async () => {
  model = await request('config');
  const versions = versionPanel(request, base);
  for (const spec of model.operations) {
    const button = document.createElement('button');
    button.textContent = spec.label;
    button.dataset.mode = spec.id;
    button.addEventListener('click', () => packet && renderOperation(spec.id));
    $(spec.group === 'plan' ? 'plan-stages' : spec.optional ? 'review-stages' : 'stages').append(button);
  }
  bind('sample', () => choose('sample'));
  bind('review-sample', () => choose('review-sample'));
  bind('plan-sample', () => choose('plan-sample'));
  bind('alternate', () => choose('alternate'));
  bind('evaluate', async () => {
    show(await request('evaluate', source()));
    note('已完成数据归约');
  });
  bind('upload', async () => {
    const file = $('upload').files[0];
    if (!file) return;
    if (file.size > 512 * 1024) throw new Error('数据包大于 512 KiB');
    $('source-pane').open = true;
    $('source').value = await file.text();
    show(await request('evaluate', source()));
    note('文件已导入并处理');
  }, 'change');
  bind('seal', async () => {
    const { revision: result, packet: saved } = await versions.seal(source());
    show(saved);
    await versions.refresh(result.digest);
    note(result.created ? `已封存第 ${result.sequence} 版` : `与第 ${result.sequence} 版相同，未重复入库`);
  });
  bind('inspect', async () => {
    const saved = await versions.inspect();
    $('source').value = JSON.stringify(saved.source, null, 2);
    show(saved);
    note('已打开封存版本');
  });
  bind('compare', async () => {
    const { left, right, rows } = await versions.compare();
    for (const button of stageButtons()) button.setAttribute('aria-pressed', 'false');
    $('table-title').textContent = '版本差异';
    renderRows(model, rows, ['id', 'stage', 'state', 'before', 'after'], '差异', { ...model.columns, before: '参照版本', after: '目标版本' });
    note(`已比较 ${left.slice(0, 8)} 与 ${right.slice(0, 8)}`);
  });
  bind('recheck', async () => {
    const digest = $('right-version').value;
    if (!digest) throw new Error('先选择一份封存计划');
    let take;
    try {
      take = JSON.parse($('recheck-take').value);
    } catch {
      throw new Error('试录数据不是有效的 JSON');
    }
    const result = await request('recheck', { plan: digest, take });
    const rows = [
      ...result.intervals.map((row) => ({ ...row, stage: '音程核对' })),
      ...result.references.map((row) => ({ ...row, stage: '参考管核对' })),
    ];
    for (const button of stageButtons()) button.setAttribute('aria-pressed', 'false');
    $('table-title').textContent = '音程复验';
    renderRows(model, rows, ['id', 'stage', 'offset', 'drift', 'state'], '复验');
    note(`复验结果：${model.values[result.state] || result.state}`);
  });
  await versions.refresh();
  await choose('sample');
});
