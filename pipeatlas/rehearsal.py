from bisect import bisect_right

from .contract import ContractError, enum, ident, invariant, num, obj, records

SEATS = ('near', 'far')
MARKS = ('valid', 'clipped', 'invalid')
# 有证半开时间段只接受有效记录；剪波与失效记录不构成证据。
CERTIFIED_MARK = 'valid'


def _window(value, label):
    item = obj(value, label)
    start = num(item.get('start'), label + '起点', 0, 1e6)
    end = num(item.get('end'), label + '终点', 0, 1e6)
    if start >= end:
        raise ContractError(f'{label}终点必须大于起点')
    return start, end


def _parse_bands(source):
    bands = []
    for band in records(source.get('bands'), '频带定义', 24, 1):
        low = num(band.get('low'), '频带下界', 0, 160000)
        high = num(band.get('high'), '频带上界', 0, 160000)
        if low >= high:
            raise ContractError('频带上界必须大于下界')
        bands.append(dict(id=band['id'], low=low, high=high))
    return bands


def _parse_criteria(source, band_ids):
    criteria = obj(source.get('criteria'), '比对口径')
    selections = criteria.get('bands')
    invariant(isinstance(selections, list) and selections, '待比较频带不能为空')
    chosen = [ident(key, '待比较频带') for key in selections]
    invariant(len(chosen) == len(set(chosen)), '待比较频带不能重复')
    unknown = next((key for key in chosen if key not in band_ids), None)
    if unknown is not None:
        raise ContractError(f'待比较频带未定义：{unknown}')
    ratio_low = num(criteria.get('ratio_low'), '合格比值下界', 0, 1e9)
    ratio_high = num(criteria.get('ratio_high'), '合格比值上界', 0, 1e9)
    if ratio_low > ratio_high:
        raise ContractError('合格比值范围上下界颠倒')
    coverage = num(criteria.get('min_coverage'), '最短合格覆盖比例', 0, 1)
    min_support = num(criteria.get('min_support'), '最短共同支持时长', 0, 1e6)
    return dict(bands=chosen, ratio_low=ratio_low, ratio_high=ratio_high,
                min_coverage=coverage, min_support=min_support)


def _parse_seat(seat, take, band_ids):
    parsed = obj(take, f'{seat}席合奏')
    roster = parsed.get('opened')
    invariant(isinstance(roster, list) and roster, '开管名单不能为空')
    opened = [ident(key, '开管标识') for key in roster]
    invariant(len(opened) == len(set(opened)), '开管名单不能重复')
    windows, measures = {band: [] for band in band_ids}, []
    for record in records(parsed.get('measurements'), '分频带测量', 600, 1):
        band = ident(record.get('band'), '测量频带')
        if band not in band_ids:
            raise ContractError(f'测量引用了未定义频带：{band}')
        start, end = _window(record, '测量时段')
        actual_low = num(record.get('band_low'), '实际频带下界', 0, 160000)
        actual_high = num(record.get('band_high'), '实际频带上界', 0, 160000)
        if actual_low >= actual_high:
            raise ContractError('实际频带上界必须大于下界')
        power = num(record.get('power'), '测量功率', 0, 1e12)
        factor = num(record.get('factor'), '功率换算系数', 1e-9, 1e9)
        background = num(record.get('background'), '同口径背景功率', 0, 1e12)
        mark = enum(record.get('mark'), MARKS, '测量标记')
        row = dict(id=record['id'], band=band, start=start, end=end,
                   band_low=actual_low, band_high=actual_high, power=power,
                   factor=factor, background=background, mark=mark)
        measures.append(row)
        if mark == CERTIFIED_MARK:
            windows[band].append((start, end, record['id']))
    return dict(opened=opened, measures=measures, windows=windows)


def _certified(windows, label):
    # 保留每条记录自己的半开 [start, end)；同席同频带有证时段不得重叠，相邻允许。
    ordered = sorted(windows)
    for earlier, later in zip(ordered, ordered[1:]):
        if later[0] < earlier[1]:
            raise ContractError(f'{label}存在重叠的有证测量时段：{later[2]}')
    return [(start, end, [record_id]) for start, end, record_id in ordered]


def _containing(intervals, point):
    starts = [item[0] for item in intervals]
    index = bisect_right(starts, point) - 1
    if index >= 0 and intervals[index][0] <= point < intervals[index][1]:
        return index
    return None


def _net(row):
    # 线性功率先换算、再扣背景；低于背景无可信净功率，等于背景为有证的零。
    gross = row['power'] * row['factor']
    if gross < row['background']:
        return None
    if gross == row['background']:
        return 0.0
    return gross - row['background']


def _band_segments(band_id, near, far, measures):
    near_intervals = _certified(near['windows'][band_id], f'近席频带 {band_id}')
    far_intervals = _certified(far['windows'][band_id], f'远席频带 {band_id}')
    near_lookup = {row['id']: row for row in measures['near'] if row['band'] == band_id}
    far_lookup = {row['id']: row for row in measures['far'] if row['band'] == band_id}
    boundaries = sorted({point for start, end, _ in near_intervals + far_intervals
                         for point in (start, end)})
    segments = []
    for left, right in zip(boundaries, boundaries[1:]):
        near_index = _containing(near_intervals, left)
        far_index = _containing(far_intervals, left)
        if near_index is None or far_index is None:
            continue
        near_evidence = [key for key in near_intervals[near_index][2]
                         if near_lookup[key]['start'] <= left < near_lookup[key]['end']]
        far_evidence = [key for key in far_intervals[far_index][2]
                        if far_lookup[key]['start'] <= left < far_lookup[key]['end']]
        segments.append(dict(start=left, end=right, near=near_evidence, far=far_evidence))
    return segments, near_intervals, far_intervals


def _segment_facts(segment, near_lookup, far_lookup):
    near_rows = [near_lookup[key] for key in segment['near']]
    far_rows = [far_lookup[key] for key in segment['far']]
    near_values = [_net(row) for row in near_rows]
    far_values = [_net(row) for row in far_rows]
    # 共同支持内任一席低于背景，该子段就没有可信净功率，不参与比值、能量与覆盖。
    if any(value is None for value in near_values + far_values):
        return dict(near_net=None, far_net=None, ratio=None, rated=False, defined=False)
    near_total = sum(near_values)
    far_total = sum(far_values)
    defined = near_total != 0  # 近席为零（含两席皆零）时，远近席功率比无定义。
    ratio = far_total / near_total if defined else None
    return dict(near_net=near_total, far_net=far_total, ratio=ratio,
                rated=defined, defined=defined)


def analyze_rehearsal(rehearsal, opened_roster):
    source = obj(rehearsal, '试录数据')
    ident(source.get('id'), '试录批次标识')
    bands = _parse_bands(source)
    band_ids = {band['id'] for band in bands}
    band_bounds = {band['id']: (band['low'], band['high']) for band in bands}
    criteria = _parse_criteria(source, band_ids)
    takes = obj(source.get('takes'), '两场合奏')
    seats = {}
    for seat in SEATS:
        if seat not in takes:
            raise ContractError('缺少近席或远席合奏')
        seats[seat] = _parse_seat(seat, takes[seat], band_ids)
    # 两场必须使用同一份开管名单及频带定义。
    near_opened, far_opened = seats['near']['opened'], seats['far']['opened']
    if near_opened != far_opened:
        raise ContractError('两席开管名单不一致，不能比对')
    if near_opened != list(opened_roster):
        raise ContractError('试录开管名单必须与主开管名单完全一致')
    measures = {seat: seats[seat]['measures'] for seat in SEATS}
    for seat in SEATS:
        for row in measures[seat]:
            low, high = band_bounds[row['band']]
            if row['band_low'] != low or row['band_high'] != high:
                raise ContractError(f'测量 {row["id"]} 的实际频带与频带定义不一致')

    rows, evidence_rows = [], []
    for band_id in criteria['bands']:
        segments, near_intervals, far_intervals = _band_segments(
            band_id, seats['near'], seats['far'], measures)
        near_lookup = {row['id']: row for row in measures['near'] if row['band'] == band_id}
        far_lookup = {row['id']: row for row in measures['far'] if row['band'] == band_id}
        support_duration = 0.0
        qualified_duration = 0.0
        near_energy = 0.0
        far_energy = 0.0
        over_limit = False
        undefined_ratio = False
        band_evidence = []
        for segment in segments:
            duration = segment['end'] - segment['start']
            support_duration += duration
            facts = _segment_facts(segment, near_lookup, far_lookup)
            if facts['near_net'] is not None:
                near_energy += facts['near_net'] * duration
                far_energy += facts['far_net'] * duration
            if facts['rated']:
                if criteria['ratio_low'] <= facts['ratio'] <= criteria['ratio_high']:
                    qualified_duration += duration
                else:
                    over_limit = True
            elif facts['near_net'] is not None:
                undefined_ratio = True
            evidence_id = f'{band_id}/{len(band_evidence) + 1}'
            band_evidence.append(evidence_id)
            evidence_rows.append(dict(
                id=evidence_id, band=band_id,
                start=segment['start'], end=segment['end'], duration=duration,
                near_net=facts['near_net'], far_net=facts['far_net'],
                ratio=facts['ratio'],
                state='rated' if facts['rated'] else (
                    'undefined' if facts['near_net'] is not None else 'unrated'),
                evidence={'near': segment['near'], 'far': segment['far']}))
        # 两个指标使用同一共同支持范围，且不得相互代替。
        coverage = qualified_duration / support_duration if support_duration > 0 else None
        energy_ratio = far_energy / near_energy if near_energy > 0 else None
        support_ok = support_duration >= criteria['min_support']
        coverage_ok = coverage is not None and coverage >= criteria['min_coverage']
        # 共同支持不足优先于超限判定；覆盖不足与已证超限分别说明。
        if not support_ok:
            state = 'short'
        elif over_limit or undefined_ratio:
            state = 'over'
        elif coverage_ok:
            state = 'verified'
        else:
            state = 'short'
        rows.append(dict(
            id=band_id, state=state, support=support_duration,
            qualified=qualified_duration, coverage=coverage,
            energy_ratio=energy_ratio, ratio_low=criteria['ratio_low'],
            ratio_high=criteria['ratio_high'], min_support=criteria['min_support'],
            min_coverage=criteria['min_coverage'],
            evidence={'segments': band_evidence,
                      'near_windows': [{'start': s, 'end': e, 'records': rec}
                                       for s, e, rec in near_intervals],
                      'far_windows': [{'start': s, 'end': e, 'records': rec}
                                      for s, e, rec in far_intervals]}))
    return dict(rehearsal=rows, rehearsal_evidence=evidence_rows)
