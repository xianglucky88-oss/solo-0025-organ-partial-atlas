const ns = 'http://www.w3.org/2000/svg';
function mark(svg, tag, attrs, text) {
  const node = document.createElementNS(ns, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (text !== undefined) node.textContent = text;
  svg.append(node);
  return node;
}
const line = (svg, x1, y1, x2, y2, cls = 'line') => mark(svg, 'line', { x1, y1, x2, y2, class: cls });
const rect = (svg, x, y, width, height, cls) => mark(svg, 'rect', { x, y, width, height, class: cls });
const dot = (svg, cx, cy, cls, r = 3) => mark(svg, 'circle', { cx, cy, r, class: cls });
const text = (svg, x, y, value) => mark(svg, 'text', { x, y }, value);
const path = (svg, points, cls) => mark(svg, 'polyline', { points: points.map((p) => p.join(',')).join(' '), class: cls });
function scale(lo, hi, a, b) { return (n) => a + (b - a) * (n - lo) / (hi - lo || 1); }
function range(values, fallback = [0, 1]) { return values.length ? [Math.min(...values), Math.max(...values)] : fallback; }
function grid(svg, a, b, unit) {
  for (let index = 0; index <= 5; index++) {
    const x = 58 + index * 122;
    line(svg, x, 30, x, 197);
    text(svg, x - 7, 217, Number((a + (b - a) * index / 5).toPrecision(4)));
  }
  text(svg, 686, 217, unit);
}

function render(svg, { outputs }) {
    const peaks = outputs.candidate;
    const top = Math.max(1, ...peaks.map((p) => p.hz));
    const high = Math.max(0.01, ...peaks.map((p) => p.amplitude));
    const x = scale(0, top, 58, 668);
    const y = scale(0, high, 196, 40);
    grid(svg, 0, top, '赫兹');
    const certified = (outputs.certificates ?? []).find((row) => row.id === '基线');
    if (certified) {
      for (const [lo, hi] of certified.effective) {
        if (lo > top) continue;
        rect(svg, x(lo), 30, x(Math.min(hi, top)) - x(lo), 166, 'band');
      }
      for (const part of certified.polluted) {
        if (part.lo > top) continue;
        rect(svg, x(part.lo), 30, x(Math.min(part.hi, top)) - x(part.lo), 166, 'band alert');
      }
    }
    for (const [i, p] of peaks.entries()) {
      const state = outputs.assign[i].state;
      const cls = state === 'assigned' ? 'series' : state === 'ambiguous' ? 'series alert' : 'series secondary';
      line(svg, x(p.hz), 196, x(p.hz), y(p.amplitude), cls);
    }
    for (const target of outputs.target.filter((p) => p.state === 'open' && p.hz <= top)) dot(svg, x(target.hz), 193, 'empty-mark', 4);
    text(svg, 58, 24, certified ? '阴影为有证且未污染频带，警示带为陷波或剪波' : '标记为目标基音位置');
}

export function draw(svg, packet, config) {
  svg.replaceChildren();
  mark(svg, 'title', {}, config.plot);
  render(svg, packet);
}
