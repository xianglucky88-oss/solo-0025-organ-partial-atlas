from .contract import ident, invariant, num, obj, unique_order

# 浮点求交的容差，只用于抵消频率取整带来的末位误差，不放宽登记限本身。
EPS = 1e-9


def parse_profiles(review, model, takes):
    """登记经台架确认的各管形变系数、核对阶次、音分误差限与形变允差。"""
    pipe_ids = {p['id']: p for p in model['pipes']}
    take_ids = {key for key, _, _ in takes}
    take_peaks = {key: {p['id'] for p in conf['peaks']} for key, conf, _ in takes}
    take_opened = {key: set(conf['opened']) for key, conf, _ in takes}
    entries = obj(review, '复核数据').get('deformation', [])
    invariant(isinstance(entries, list) and 0 <= len(entries) <= len(pipe_ids), '形变登记条数不合法')
    profiles = []
    seen_pipes = set()
    for row in entries:
        row = obj(row, '形变登记')
        pipe = ident(row.get('pipe'), '形变音管')
        invariant(pipe not in seen_pipes, f'同一音管重复登记形变：{pipe}')
        seen_pipes.add(pipe)
        invariant(pipe in pipe_ids, f'形变登记引用未知音管：{pipe}')
        take_names = row.get('takes')
        invariant(isinstance(take_names, list) and len(take_names) == 2, '每场交接必须预先指定两场独奏')
        first, second = ident(take_names[0], '独奏标识'), ident(take_names[1], '独奏标识')
        invariant(first != second, '两场交接独奏不能是同一场')
        for name in (first, second):
            invariant(name in take_ids, f'形变登记引用未知独奏：{name}')
            invariant(take_opened[name] == {pipe}, f'{name} 不是 {pipe} 的单管独奏')
        shared = take_peaks[first] & take_peaks[second]
        invariant(not shared, '两场交接独奏的原峰互不复用，发现共用峰：' + '、'.join(sorted(shared)))
        limit = num(row.get('limit'), '形变允差', 0, 1000)
        orders = obj(row, '形变登记').get('orders')
        invariant(isinstance(orders, list) and 3 <= len(orders) <= 12, '核对阶次应含 3 至 12 条登记')
        parsed_orders = []
        for order in orders:
            order = obj(order, '核对阶次')
            harmonic = num(order.get('harmonic'), '泛音阶次', 1, 12)
            invariant(type(harmonic) is int, '泛音阶次需为整数')
            coefficient = num(order.get('coefficient'), '形变系数', -100, 100)
            bound = num(order.get('limit'), '该阶音分误差限', 0, 100)
            invariant(bound > 0, '该阶音分误差限必须为正')
            parsed_orders.append(dict(harmonic=harmonic, coefficient=coefficient, limit=bound))
        unique_order(parsed_orders, 'harmonic', '核对阶次')
        invariant(parsed_orders[0]['harmonic'] == 1, '核对阶次必须包含基音（第一阶）')
        invariant(parsed_orders[0]['coefficient'] == 0, '基音形变系数必须登记为零')
        invariant(len(parsed_orders) >= 3, '至少要登记基音和两个高阶阶次')
        distinct = {o['coefficient'] for o in parsed_orders if o['harmonic'] > 1}
        invariant(len(distinct) >= 2, '高阶阶次至少要有两个不同的形变系数')
        maximum = pipe_ids[pipe]['maximum']
        invariant(all(o['harmonic'] <= maximum for o in parsed_orders), '核对阶次超出该音管泛音槽上限')
        profiles.append(dict(pipe=pipe, takes=[first, second], limit=limit, orders=parsed_orders))
    return profiles


def _assigned_peaks(output):
    """只采用已经无争议归属（双端唯一 assigned）的峰。"""
    return {r['harmonic']: r for r in output['assign'] if r['state'] == 'assigned'}


def joint_range(observations, limits):
    """
    全部阶次共用同一个整体偏移 b 和同一个形变量 x：
        d_i = b + c_i x，且 |d_i - (b + c_i x)| <= e_i
    两两消去 b，对每对阶次得到 x 的线性夹逼，再对所有夹逼求交。
    各阶单独算出的量不允许拼成解释，所以这里只返回全体共同可行区间。
    """
    low, high = float('-inf'), float('inf')
    per_order = {item['harmonic']: [float('-inf'), float('inf')] for item in observations}
    for left in observations:
        for right in observations:
            if left['harmonic'] == right['harmonic']:
                continue
            slope = left['coefficient'] - right['coefficient']
            if slope == 0:
                continue
            delta = left['deviation'] - right['deviation']
            span = limits[left['harmonic']] + limits[right['harmonic']]
            edge_low = (delta - span) / slope
            edge_high = (delta + span) / slope
            if edge_low > edge_high:
                edge_low, edge_high = edge_high, edge_low
            low = max(low, edge_low)
            high = min(high, edge_high)
            for harmonic in (left['harmonic'], right['harmonic']):
                per_order[harmonic][0] = max(per_order[harmonic][0], edge_low)
                per_order[harmonic][1] = min(per_order[harmonic][1], edge_high)
    return low, high, per_order


def _analyze_take(profile, take_id, output):
    registered = {o['harmonic']: o for o in profile['orders']}
    limits = {n: o['limit'] for n, o in registered.items()}
    assigned = _assigned_peaks(output)
    observations = []
    for harmonic, order in sorted(registered.items()):
        row = assigned.get(harmonic)
        if row is None:
            continue
        observations.append(dict(harmonic=harmonic, coefficient=order['coefficient'],
                                 deviation=row['deviation'], peak=row['id']))
    has_base = any(o['harmonic'] == 1 for o in observations)
    high_coefficients = {o['coefficient'] for o in observations if o['harmonic'] > 1}
    result = dict(id=take_id, state='inconclusive', range=[None, None], band=None,
                  evidence=dict(peaks=[o['peak'] for o in observations],
                               orders=[dict(harmonic=o['harmonic'], coefficient=o['coefficient'],
                                            deviation=o['deviation'], peak=o['peak']) for o in observations]))
    # 缺少可信基音或不足两个系数不同的可信高阶时不下结论。
    if not has_base or len(high_coefficients) < 2:
        result['evidence']['reason'] = '缺少可信基音或不足两个系数不同的可信高阶'
        return result
    low, high, per_order = joint_range(observations, limits)
    for item in result['evidence']['orders']:
        item['x_band'] = per_order[item['harmonic']]
    if low > high + EPS:
        result['state'] = 'mismatch'
        result['evidence']['reason'] = '证据齐全但不存在共同的整体偏移与形变量解释'
        return result
    result['state'] = 'feasible'
    result['range'] = [low, high]
    result['band'] = _classify_band(low, high, profile['limit'])
    return result


def _classify_band(low, high, limit):
    if high < -limit - EPS:
        return 'below'
    if low > limit + EPS:
        return 'above'
    if low >= -limit - EPS and high <= limit + EPS:
        return 'within'
    return 'crossing'


def deformation_handoff(profile, take_outputs, baseline_tune):
    take_rows = [_analyze_take(profile, take_id, output) for take_id, output in take_outputs]
    baseline = next((r for r in baseline_tune if r['id'] == profile['pipe']), None)
    evidence = dict(takes=[r['id'] for r in take_rows],
                    ranges={r['id']: r['range'] for r in take_rows},
                    bands={r['id']: r['band'] for r in take_rows},
                    peaks={r['id']: r['evidence']['peaks'] for r in take_rows},
                    baseline=baseline['state'] if baseline else 'pending')
    states = {r['state'] for r in take_rows}
    if states == {'feasible'}:
        bands = {r['band'] for r in take_rows}
        if bands == {'above'} or bands == {'below'}:
            state = 'persistent'
        elif bands == {'within'}:
            # 两场都只有整体音分偏移、形变在允差内：调律仍按原有基音证据判断。
            state = baseline['state'] if baseline else 'pending'
        else:
            # 一场超限，或任一有证范围跨越限值，都只能留下待复测。
            state = 'revisit'
    elif 'mismatch' in states:
        state = 'mismatch'
    else:
        state = 'inconclusive'
    return dict(id=profile['pipe'], state=state, limit=profile['limit'],
                takes=take_rows, evidence=evidence)


def analyze_deformation(review, model, takes, outputs):
    profiles = parse_profiles(review, model, takes)
    take_index = {key: out for key, _, out in takes}
    rows = []
    for profile in profiles:
        take_outputs = [(name, take_index[name]) for name in profile['takes']]
        rows.append(deformation_handoff(profile, take_outputs, outputs['tune']))
    return rows
