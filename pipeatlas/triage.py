from collections import defaultdict


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


def silent_openings(takes, outputs, pipes):
    result = []
    baseline = {r['id']: r['state'] for r in outputs['coverage']}
    for row in pipes:
        observed, ambiguous, absent = [], [], []
        for key, conf, out in takes:
            if row['id'] not in conf['opened']:
                continue
            state = next(r['state'] for r in out['coverage'] if r['id'] == row['id'])
            (observed if state == 'covered' else ambiguous if state in ('ambiguous', 'overtone') else absent).append(key)
        result.append(dict(id=row['id'], count=len(observed)+len(ambiguous)+len(absent), observed=len(observed),
                           state='covered' if observed else 'partial' if ambiguous else 'missing' if absent else 'blind',
                           evidence={'base': baseline.get(row['id'], 'closed'), 'observed': observed, 'uncertain': ambiguous, 'absent': absent}))
    return result
