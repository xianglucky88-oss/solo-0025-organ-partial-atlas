from math import sqrt

from .contract import ContractError, ident, invariant, num, obj, records
from .domain import parse
from .harmonics import evaluate
from .rules import decide

# 档位枚举只面向本机小批量归约，超出规模按非法输入退回
SEARCH_LIMIT = 2_000_000


def read_plan(plan, model):
    plan = obj(plan, '作业计划')
    valid = {pipe['id'] for pipe in model['pipes']}
    intervals = []
    for row in records(plan.get('intervals'), '音程约束', 96, 1):
        low = ident(row.get('low'), '音程低端')
        high = ident(row.get('high'), '音程高端')
        invariant(low in valid and high in valid, '音程约束引用了名册外的音管')
        invariant(low != high, '音程两端不能是同一支管')
        lo = num(row.get('lo'), '音程差值下界', -1200, 1200)
        hi = num(row.get('hi'), '音程差值上界', -1200, 1200)
        invariant(lo <= hi, '音程差值下界不能高于上界')
        intervals.append(dict(id=row['id'], low=low, high=high, lo=lo, hi=hi))
    actions = {}
    for row in records(plan.get('actions'), '动作档位', 24, 1):
        pipe = row['id']
        invariant(pipe in valid, '动作档位引用了名册外的音管')
        steps = row.get('steps')
        invariant(isinstance(steps, list) and 1 <= len(steps) <= 41, '动作档位条数不合法')
        invariant(all(type(step) is int and abs(step) <= 600 for step in steps), '动作档位应为 ±600 内的整数音分')
        invariant(len(set(steps)) == len(steps), '动作档位不能重复')
        invariant(0 in steps, '动作档位必须登记零动作')
        actions[pipe] = dict(steps=sorted(steps), cost=num(row.get('cost'), '非零动作代价', 0, 1e6))
    used = {pipe for row in intervals for pipe in (row['low'], row['high'])}
    invariant(used <= set(actions), '音程两端的音管都要登记动作档位')
    return dict(intervals=intervals, actions=actions)


def _components(intervals):
    parent = {}

    def root(pipe):
        parent.setdefault(pipe, pipe)
        while parent[pipe] != pipe:
            parent[pipe] = parent[parent[pipe]]
            pipe = parent[pipe]
        return pipe

    for row in intervals:
        parent[root(row['low'])] = root(row['high'])
    groups = {}
    for row in intervals:
        group = groups.setdefault(root(row['low']), dict(pipes=set(), intervals=[]))
        group['pipes'].update((row['low'], row['high']))
        group['intervals'].append(row)
    return list(groups.values())


def _solve(pipes, constraints):
    # 目标序：总动作代价、总绝对动作量、按音管标识序的动作序列，均取小者
    size = len(pipes)
    domains = [sorted(steps, key=lambda step: (step != 0, abs(step), step)) for _, steps, _ in pipes]
    linked = [[] for _ in range(size)]
    for index, (low, high, *_rest) in enumerate(constraints):
        linked[low].append(index)
        linked[high].append(index)
    assign = [None] * size
    best = None
    nodes = 0

    def consistent(pipe):
        for index in linked[pipe]:
            low, high, lo, hi, offset = constraints[index]
            a, b = assign[low], assign[high]
            if a is None and b is None:
                continue
            if a is not None and b is not None:
                if not lo <= offset + b - a <= hi:
                    return False
            elif a is None:
                if not any(lo <= offset + b - step <= hi for step in domains[low]):
                    return False
            elif not any(lo <= offset + step - a <= hi for step in domains[high]):
                return False
        return True

    def search(pipe, cost, total):
        nonlocal best, nodes
        nodes += 1
        if nodes > SEARCH_LIMIT:
            raise ContractError('音程计划超出本机归约规模')
        if best is not None and (cost, total) > best[:2]:
            return
        if pipe == size:
            key = (cost, total, tuple(assign))
            if best is None or key < best:
                best = key
            return
        for step in domains[pipe]:
            assign[pipe] = step
            if consistent(pipe):
                search(pipe + 1, cost + (pipes[pipe][2] if step else 0), total + abs(step))
        assign[pipe] = None

    search(0, 0, 0)
    return None if best is None else {pipes[k][0]: action for k, action in enumerate(best[2])}


def _shared(group):
    return sorted(pipe for pipe in group['pipes']
                  if sum(pipe in (row['low'], row['high']) for row in group['intervals']) > 1)


def plan_work(raw, model, outputs):
    # 只读基线归约结果，不改动已有频峰归属；缺可信基音标证据不足，不当零
    plan = read_plan(raw, model)
    deviation = {row['id']: row['deviation'] for row in outputs['tune']}
    rows, measurable = [], []
    for item in plan['intervals']:
        evidence = dict(low=item['low'], high=item['high'], lo=item['lo'], hi=item['hi'])
        low_dev, high_dev = deviation.get(item['low']), deviation.get(item['high'])
        missing = [item['low']] * (low_dev is None) + [item['high']] * (high_dev is None)
        if missing:
            rows.append(dict(id=item['id'], offset=None, predicted=None, state='pending',
                             evidence=dict(evidence, missing=missing)))
            continue
        row = dict(id=item['id'], offset=high_dev - low_dev, predicted=None, state=None, evidence=evidence)
        rows.append(row)
        measurable.append(dict(item, offset=row['offset'], row=row))
    blocked, solution = [], {}
    for group in _components(measurable):
        pipes = sorted(group['pipes'])
        index = {pipe: k for k, pipe in enumerate(pipes)}
        constraints = [(index[row['low']], index[row['high']], row['lo'], row['hi'], row['offset'])
                       for row in group['intervals']]
        solved = _solve([(pipe, plan['actions'][pipe]['steps'], plan['actions'][pipe]['cost']) for pipe in pipes],
                        constraints)
        if solved is None:
            blocked.append(group)
        else:
            solution.update(solved)
    issued = not blocked and all(row['state'] != 'pending' for row in rows)
    for group in blocked:
        ids = sorted(row['id'] for row in group['intervals'])
        for row in group['intervals']:
            row['row']['state'] = 'blocked'
            row['row']['evidence'].update(intervals=ids, pipes=_shared(group))
    if issued:
        for row in measurable:
            net = solution[row['high']] - solution[row['low']]
            row['row'].update(predicted=row['offset'] + net, state='ready' if net == 0 else 'adjust')
    else:
        for row in measurable:
            if row['row']['state'] is None:
                row['row']['state'] = 'withheld'
    moved = sorted(pipe for pipe, action in solution.items() if action)
    if issued:
        summary = dict(id='作业计划', state='planned',
                       cost=sum(plan['actions'][pipe]['cost'] for pipe in moved),
                       moves=sum(abs(solution[pipe]) for pipe in moved), count=len(moved),
                       evidence=dict(moved=moved))
    elif blocked:
        summary = dict(id='作业计划', state='blocked', cost=None, moves=None, count=None,
                       evidence=dict(intervals=sorted({row['id'] for group in blocked for row in group['intervals']}),
                                     pipes=sorted({pipe for group in blocked for pipe in _shared(group)})))
    else:
        pending = [row for row in rows if row['state'] == 'pending']
        summary = dict(id='作业计划', state='pending', cost=None, moves=None, count=None,
                       evidence=dict(intervals=sorted(row['id'] for row in pending),
                                     pipes=sorted({pipe for row in pending for pipe in row['evidence']['missing']})))
    actions = []
    for pipe, spec in plan['actions'].items():
        evidence = dict(steps=spec['steps'], price=spec['cost'])
        if spec['steps'] == [0]:
            actions.append(dict(id=pipe, action=0, cost=0, state='fixed', evidence=evidence))
        elif issued:
            action = solution.get(pipe, 0)
            actions.append(dict(id=pipe, action=action, cost=spec['cost'] if action else 0,
                                state='adjust' if action else 'kept', evidence=evidence))
        else:
            actions.append(dict(id=pipe, action=None, cost=None, state='withheld', evidence=evidence))
    return dict(program=[summary], intervals=rows, actions=actions)


def recheck(packet, take):
    # 复验条件以封存计划为准：约定区间、登记允差与温度口径都读封存版本
    source = packet['source']
    if not isinstance(source.get('plan'), dict):
        raise ContractError('该成果版本不含音程作业计划', 409)
    model = parse(source)
    plan = read_plan(source['plan'], model)
    take = obj(take, '复验试录')
    key = ident(take.get('id'), '复验标识')
    valid = {pipe['id'] for pipe in model['pipes']}
    opened = take.get('opened')
    invariant(isinstance(opened, list) and len(opened) <= len(valid), '复验开管名单无效')
    opened = [ident(pipe, '复验音管') for pipe in opened]
    invariant(len(set(opened)) == len(opened) and set(opened) <= valid, '复验名单有未知音管或重复')
    peaks = [dict(id=row['id'], hz=num(row.get('hz'), '复验频率', 1, 160000),
                  amplitude=num(row.get('amplitude'), '复验幅值', 0, 100))
             for row in records(take.get('peaks'), '复验频峰', 100)]
    temperature = num(take.get('temperature'), '复验温度', 0, 40)
    configuration = dict(model, opened=set(opened), peaks=peaks,
                         ratio=sqrt((temperature + 273.15) / (model['reference'] + 273.15)))
    outcome = decide(configuration, evaluate(configuration))
    measured = {row['id']: row['deviation'] for row in outcome['tune']}
    baseline = {row['id']: row['deviation'] for row in packet['outputs']['tune']}
    intervals = []
    for item in plan['intervals']:
        evidence = dict(low=item['low'], high=item['high'], lo=item['lo'], hi=item['hi'])
        low_dev, high_dev = measured.get(item['low']), measured.get(item['high'])
        missing = [item['low']] * (low_dev is None) + [item['high']] * (high_dev is None)
        if missing:
            intervals.append(dict(id=item['id'], offset=None, state='pending',
                                  evidence=dict(evidence, missing=missing)))
            continue
        offset = high_dev - low_dev
        intervals.append(dict(id=item['id'], offset=offset,
                              state='held' if item['lo'] <= offset <= item['hi'] else 'violated',
                              evidence=dict(evidence, readings={item['low']: low_dev, item['high']: high_dev})))
    references = []
    for pipe, spec in plan['actions'].items():
        if spec['steps'] != [0]:
            continue
        before, after = baseline.get(pipe), measured.get(pipe)
        evidence = dict(baseline=before, tolerance=model['passband'])
        if before is None or after is None:
            references.append(dict(id=pipe, deviation=after, drift=None, state='pending', evidence=evidence))
            continue
        drift = after - before
        references.append(dict(id=pipe, deviation=after, drift=drift,
                               state='held' if abs(drift) <= model['passband'] else 'drift',
                               evidence=evidence))
    states = [row['state'] for row in intervals + references]
    overall = 'pending' if 'pending' in states else 'rejected' if any(state != 'held' for state in states) else 'accepted'
    return dict(plan=packet['digest'], take=key, subject=packet['subject'], state=overall,
                intervals=intervals, references=references)
