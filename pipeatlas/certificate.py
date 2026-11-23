from .contract import enum, interval, invariant, num, obj

KINDS = {'notch', 'clip'}
LIMIT = 64
HZ_LIMIT = 160000


def _merge(ranges):
    merged = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _subtract(ranges, holes):
    pieces = list(ranges)
    for hlo, hhi in _merge(holes):
        remaining = []
        for lo, hi in pieces:
            if hhi <= lo or hlo >= hi:
                remaining.append((lo, hi))
            else:
                if lo < hlo:
                    remaining.append((lo, hlo))
                if hhi < hi:
                    remaining.append((hhi, hi))
        pieces = remaining
    return _merge(pieces)


def parse(value, floor, label='频带证书'):
    if value is None:
        return None
    cert = obj(value, label)
    stated = num(cert.get('floor'), f'{label}幅值门限', 0, 100)
    invariant(stated == floor, f'{label}幅值门限必须与本场幅值标尺一致')
    bands = cert.get('bands')
    invariant(isinstance(bands, list) and len(bands) <= LIMIT,
              f'{label}频段应不超过 {LIMIT} 个')
    claimed = []
    for item in bands:
        band = obj(item, f'{label}频段')
        claimed.append(interval(band.get('lo'), band.get('hi'), f'{label}频段', HZ_LIMIT))
    sources = cert.get('polluted', [])
    invariant(isinstance(sources, list) and len(sources) <= LIMIT,
              f'{label}污染段应不超过 {LIMIT} 个')
    polluted, holes = [], []
    for item in sources:
        part = obj(item, f'{label}污染段')
        lo, hi = interval(part.get('lo'), part.get('hi'), f'{label}污染段', HZ_LIMIT)
        kind = enum(part.get('kind'), KINDS, f'{label}污染类型')
        polluted.append(dict(lo=lo, hi=hi, kind=kind))
        holes.append((lo, hi))
    # Touching or overlapping valid bands join. Pollution is removed only after joining, so
    # a second band that re-claims an overlapping notch or clipped stretch cannot erase it.
    merged = _merge(claimed)
    effective = _subtract(merged, holes)
    return dict(floor=stated, bands=[list(pair) for pair in merged], polluted=polluted,
                effective=[list(pair) for pair in effective])


def row(session, cert):
    if cert is None:
        return dict(id=session, floor=None, bands=[], polluted=[], gaps=[], effective=[], state='uncertified')
    claimed = _merge([tuple(pair) for pair in cert['bands']])
    gaps = _subtract(claimed, [tuple(pair) for pair in cert['effective']])
    return dict(id=session, floor=cert['floor'], bands=cert['bands'], polluted=cert['polluted'],
                gaps=[list(pair) for pair in gaps], effective=cert['effective'], state='certified')


def band_of(cert, hz):
    if cert is None:
        return 'clean'
    if any(part['lo'] <= hz <= part['hi'] for part in cert['polluted']):
        return 'polluted'
    if any(lo <= hz <= hi for lo, hi in cert['effective']):
        return 'clean'
    return 'blind'


def tolerance_band(target, tolerance):
    return target * 2 ** (-tolerance / 1200), target * 2 ** (tolerance / 1200)


def uncovered(cert, lo, hi):
    if cert is None:
        return []
    result, cursor = [], lo
    for start, stop in cert['effective']:
        if stop <= cursor:
            continue
        if start >= hi:
            break
        if start > cursor:
            result.append([cursor, min(start, hi)])
        cursor = max(cursor, stop)
        if cursor >= hi:
            break
    if cursor < hi:
        result.append([cursor, hi])
    return [piece for piece in result if piece[0] < piece[1]]


def polluted_overlap(cert, lo, hi):
    if cert is None:
        return []
    return [[max(part['lo'], lo), min(part['hi'], hi)] for part in cert['polluted']
            if part['lo'] < hi and part['hi'] > lo]


def muted_by_pollution(cert, lo, hi):
    """True when the band claims the fundamental stretch but pollution covers every hole."""
    gaps = uncovered(cert, lo, hi)
    if not gaps:
        return False
    muted = polluted_overlap(cert, lo, hi)
    return all(any(start <= glo and ghi <= stop for start, stop in muted) for glo, ghi in gaps)
