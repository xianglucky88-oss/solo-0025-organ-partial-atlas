from collections import defaultdict

from . import certificate
from . import certificate


def slot_pressure(facts, outputs):
    claims = defaultdict(list)
    for peak, edges in facts['graph'].items():
        for pipe, harmonic, deviation in edges:
            claims[f'{pipe}/{harmonic}'].append((peak, len(edges), deviation))
    assigned = {f"{r['pipe']}/{r['harmonic']}" for r in outputs['assign'] if r['state'] == 'assigned'}
    return [dict(id=slot, count=len(rows), exclusive=sum(count == 1 for _, count, _ in rows),
                 state='assigned' if slot in assigned else 'contested' if len(rows) > 1 else 'ambiguous',
                 evidence={peak: deviation for peak, _, deviation in rows}) for slot, rows in sorted(claims.items())]


def harmonic_residuals(outputs):
    groups = defaultdict(list)
    for row in outputs['assign']:
        if row['state'] == 'assigned' and row['harmonic'] > 1:
            groups[row['pipe']].append(row)
    result = []
    for row in outputs['tune']:
        known = groups[row['id']]
        if not known:
            continue
        base = row['deviation']
        for r in known:
            result.append(dict(id=r['id'], pipe=row['id'], harmonic=r['harmonic'], deviation=r['deviation'],
                               relative=None if base is None else r['deviation']-base,
                               state='pending' if base is None else 'known', evidence={'base': row['evidence'], 'peak': r['id']}))
    return result


def tune_handoff(outputs, stability_rows, isolated):
    spread = {r['id']: r for r in stability_rows}
    supported = {r['id']: r['count'] for r in isolated}
    result = []
    for row in outputs['tune']:
        pipe = row['id']
        stability = spread.get(pipe, {}).get('state')
        witnesses = row['evidence'] if isinstance(row['evidence'], list) else []
        has_lead = any(supported.get(key, 0) for key in witnesses)
        state = ('revisit' if stability == 'drift' else 'review' if has_lead else 'pending') if row['state'] == 'pending' or stability == 'drift' else (
            'adjust' if row['state'] == 'adjust' and stability == 'stable' else 'ready' if row['state'] == 'ready' and stability == 'stable' else 'pending')
        result.append(dict(id=pipe, deviation=row['deviation'], state=state,
                           evidence={'baseline': row['evidence'], 'stability': stability, 'isolated_lead': has_lead}))
    return result


def silent_openings(takes, outputs, model):
    result = []
    baseline = {r['id']: r['state'] for r in outputs['coverage']}
    certified_present = model.get('certificate') is not None or any(conf.get('certificate') for _, conf, _ in takes)
    for row in model['pipes']:
        opened = [(key, conf, out) for key, conf, out in takes if row['id'] in conf['opened']]
        observed, uncertain, absent = [], [], []
        unseen, contested, blind, other, legacy, certified, muted = [], [], [], [], [], [], []
        detail = {}
        for key, conf, out in opened:
            state = next(r['state'] for r in out['coverage'] if r['id'] == row['id'])
            cert = conf.get('certificate')
            gaps, dirty, claim = [], [], []
            if cert is not None:
                target = row['base']*conf['ratio']
                lo, hi = certificate.tolerance_band(target, model['tolerance'])
                gaps = certificate.uncovered(cert, lo, hi)
                dirty = certificate.polluted_overlap(cert, lo, hi)
                claim = [list(pair) for pair in cert['bands'] if not (pair[1] < lo or pair[0] > hi)]
            detail[key] = dict(state=state, gaps=gaps, polluted=dirty, claimed=claim, certified=cert is not None)
            if cert is None:
                # A retest without a certificate replays under the original definitions and is
                # not counted as certified evidence for the new ledger.
                legacy.append(key)
                (observed if state == 'covered' else uncertain if state in ('ambiguous', 'overtone') else absent).append(key)
                continue
            certified.append(key)
            if state == 'covered':
                observed.append(key)
            elif state == 'unseen':
                unseen.append(key)
            elif state == 'ambiguous':
                contested.append(key)
            elif state == 'muted':
                # The band claimed the fundamental but a notch or clipped stretch sat on it:
                # distinct from a chain that simply never covered the band.
                muted.append(key)
            else:
                # Heard only in another band, or a stretch left blind: neither proves the
                # fundamental, so it stays in the blind column rather than filling it.
                blind.append(key)
                if state == 'overtone':
                    other.append(key)
        if not certified_present:
            state = 'covered' if observed else 'partial' if uncertain else 'missing' if absent else 'blind'
        elif observed:
            state = 'covered'
        elif contested:
            # A peak still waiting for a unique slot keeps the pipe contested even when
            # another certified run saw no fundamental.
            state = 'contested'
        elif unseen:
            # One certified absence is not cancelled by another blind retest.
            state = 'unseen'
        elif muted:
            # The band covered the fundamental but was notched or clipped across it: a
            # different failure from never having covered the band, so keep them apart
            # rather than folding one into the other.
            state = 'mixed' if blind else 'muted'
        elif legacy and not certified:
            state = 'covered' if observed else 'partial' if uncertain else 'missing' if absent else 'blind'
        else:
            state = 'blind'
        evidence = {'base': baseline.get(row['id'], 'closed'), 'observed': observed,
                    'uncertain': uncertain, 'absent': absent}
        if certified_present:
            evidence.update(unseen=unseen, contested=contested, muted=muted, blind=blind,
                            other=other, legacy=legacy, certified=certified, detail=detail)
        total = len(observed) + len(uncertain) + len(absent) + len(unseen) + len(contested) + len(blind) + len(muted)
        result.append(dict(id=row['id'], count=total, observed=len(observed), state=state, evidence=evidence))
    return result
