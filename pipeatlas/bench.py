from .contract import ident, invariant, num, obj, records


def read_trials(review, model):
    valid = {p['id'] for p in model['pipes']}
    trials = []
    for trial in records(review.get('trials', []), '试录', 12):
        gap = num(trial.get('gap'), '最大插值间隔', 0, 3600)
        invariant(gap > 0, '最大插值间隔需为正数')
        raw = trial.get('series')
        invariant(isinstance(raw, list) and 1 <= len(raw) <= 2000, '压差序列应含 1 至 2000 个样点')
        series = []
        for point in raw:
            item = obj(point, '压差样点')
            series.append((num(item.get('t'), '样点时刻', 0, 1e6), num(item.get('pa'), '台架压差', 0, 1e5)))
        invariant(all(a[0] < b[0] for a, b in zip(series, series[1:])), '压差序列时刻必须严格递增')
        events = []
        for event in records(trial.get('events'), '开管变更', 24, 1):
            moment = num(event.get('t'), '变更时刻', 0, 1e6)
            opened = event.get('opened')
            invariant(isinstance(opened, list) and len(opened) <= len(valid), '变更开管名单无效')
            keys = [ident(key, '变更音管') for key in opened]
            invariant(set(keys) <= valid and len(keys) == len(set(keys)), '变更名单有未知音管或重复')
            window = event.get('baseline')
            invariant(isinstance(window, list) and len(window) == 2, '基准窗应为起点与终点')
            start = num(window[0], '基准窗起点', 0, 1e6)
            end = num(window[1], '基准窗终点', 0, 1e6)
            invariant(start < end <= moment, '基准窗必须位于变更之前')
            floor = num(event.get('floor'), '压力下限', 1, 1e5)
            ratio = num(event.get('ratio'), '恢复比例', 0, 1)
            invariant(ratio > 0, '恢复比例需为正数')
            hold = num(event.get('hold'), '连续维持时长', 0, 3600)
            invariant(hold > 0, '连续维持时长需为正数')
            deadline = num(event.get('deadline'), '恢复期限', 0, 1e5)
            invariant(deadline > 0, '恢复期限需为正数')
            events.append(dict(id=event['id'], t=moment, opened=frozenset(keys), window=(start, end),
                               floor=floor, ratio=ratio, hold=hold, deadline=deadline))
        trials.append(dict(id=trial['id'], gap=gap, series=series, events=events))
    return trials


def _spans(series, gap):
    # 只在最大间隔内做线性插值；更大的断档不补。
    spans = []
    for t, _ in series:
        if spans and t - spans[-1][1] <= gap:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    return spans


def _value_at(series, t):
    if t <= series[0][0]:
        return series[0][1]
    for (t0, p0), (t1, p1) in zip(series, series[1:]):
        if t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return series[-1][1]


def _average(series, start, end):
    cuts = [start] + [t for t, _ in series if start < t < end] + [end]
    total = 0.0
    for left, right in zip(cuts, cuts[1:]):
        total += (_value_at(series, left) + _value_at(series, right)) * (right - left) / 2
    return total / (end - start)


def _first_hole(spans, start, end):
    cursor = start
    for lo, hi in spans:
        if hi <= cursor:
            continue
        if lo > cursor:
            return (cursor, min(lo, end))
        cursor = max(cursor, hi)
        if cursor >= end:
            return None
    return (cursor, end) if cursor < end else None


def _baseline(event, series, gap, times):
    start, end = event['window']
    t0 = event['t']
    # 基准窗内不得另有开管变更；本段变更时刻不算窗内。
    if any(start < te <= end and te < t0 for te in times):
        return None, dict(reason='window-roster', window=[start, end])
    pre = [(t, p) for t, p in series if t < t0]
    hole = _first_hole(_spans(pre, gap), start, end)
    if hole is not None:
        return None, dict(reason='window-gap', window=[start, end], gap=[hole[0], hole[1]])
    return _average(pre, start, end), None


def _split(series, lo, hi, level):
    cuts = [lo] + [t for t, _ in series if lo < t < hi] + [hi]
    points = [(x, _value_at(series, x)) for x in cuts]
    parts = []
    for (x0, p0), (x1, p1) in zip(points, points[1:]):
        if x0 == x1:
            continue
        below0, below1 = p0 < level, p1 < level
        if below0 == below1:
            parts.append((x0, x1, 'veto' if below0 else 'clear'))
            continue
        # 真实越过压力线的时刻按线性插值取，不用第一个合格样点代替。
        cross = x0 + (level - p0) * (x1 - x0) / (p1 - p0)
        parts.append((x0, cross, 'veto' if below0 else 'clear'))
        parts.append((cross, x1, 'clear' if below0 else 'veto'))
    return [(s, e, k) for s, e, k in parts if e > s]


def _parts(series, gap, start, end, level):
    parts, cursor = [], start
    for lo, hi in _spans(series, gap):
        lo, hi = max(lo, start), min(hi, end)
        if lo >= hi:
            continue
        if lo > cursor:
            parts.append((cursor, lo, 'gap'))
        parts.extend(_split(series, lo, hi, level))
        cursor = hi
    if cursor < end:
        parts.append((cursor, end, 'gap'))
    return parts


def _conclude(parts, hold):
    clear, free = [], []
    for start, end, kind in parts:
        if kind == 'clear':
            if clear and clear[-1][1] == start:
                clear[-1][1] = end
            else:
                clear.append([start, end])
        if kind != 'veto':
            if free and free[-1][1] == start:
                free[-1][1] = end
            else:
                free.append([start, end])
    # 连续保持只能在同一观测段内计满，断档两侧不拼成一次保持。
    for start, end in clear:
        if end - start >= hold:
            return 'recovered', (start, start + hold)
    # 判延期须排除期限内任何一段达到规定时长的恢复；跨过潜在恢复时刻的缺口只能给待查。
    for lo, hi in free:
        if hi - lo >= hold:
            hole = next(((s, e) for s, e, kind in parts if kind == 'gap' and lo <= s and e <= hi), None)
            if hole is not None:
                return 'pending', hole
    return 'delayed', None


def _row(trial, event, siblings, next_t, times):
    t0 = event['t']
    row = dict(id=f"{trial['id']}/{event['id']}", opened=sorted(event['opened']),
               baseline=None, level=None, crossing=None, confirmed=None)
    if len({e['opened'] for e in siblings}) > 1:
        return dict(row, state='blocked',
                    evidence=dict(reason='contradictory', at=t0, events=sorted(e['id'] for e in siblings)))
    start, end = event['window']
    value, problem = _baseline(event, trial['series'], trial['gap'], times)
    if problem is not None:
        return dict(row, state='blocked', evidence=problem)
    level = max(event['floor'], event['ratio'] * value)
    limit = t0 + event['deadline']
    # 新负载到来即截断观察，不得用新负载下的压差补证。
    usable = [(t, p) for t, p in trial['series'] if next_t is None or t < next_t]
    parts = _parts(usable, trial['gap'], t0, limit, level)
    state, found = _conclude(parts, event['hold'])
    samples = sum(1 for t, _ in trial['series'] if start <= t <= end and t < t0)
    evidence = dict(baseline=dict(window=[start, end], value=value, samples=samples),
                    level=level, window=[t0, limit])
    if state == 'recovered':
        crossing, confirmed = found
        return dict(row, baseline=value, level=level, crossing=crossing, confirmed=confirmed,
                    state='recovered', evidence=dict(evidence, crossing=crossing, confirmed=confirmed))
    if state == 'pending':
        return dict(row, baseline=value, level=level, state='pending',
                    evidence=dict(evidence, gap=[found[0], found[1]]))
    return dict(row, baseline=value, level=level, state='delayed', evidence=evidence)


def recovery_segments(trials):
    rows = []
    for trial in trials:
        times = sorted({event['t'] for event in trial['events']})
        for event in trial['events']:
            siblings = [e for e in trial['events'] if e['t'] == event['t']]
            next_t = next((t for t in times if t > event['t']), None)
            rows.append(_row(trial, event, siblings, next_t, times))
    return rows
