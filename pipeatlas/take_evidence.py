from collections import defaultdict


def isolated_support(takes, outputs):
    confirmed = defaultdict(list)
    for key, conf, out in takes:
        if len(conf['opened']) != 1:
            continue
        for row in out['assign']:
            if row['state'] == 'assigned':
                slot = f"{row['pipe']}/{row['harmonic']}"
                confirmed[slot].append(f"{key}/{row['id']}")
    result = []
    unresolved = {r['id'] for r in outputs['conflict']}
    for row in outputs['candidate']:
        if row['id'] not in unresolved:
            continue
        evidence = {slot: confirmed[slot] for slot in row['evidence'] if slot in confirmed}
        result.append(dict(id=row['id'], count=len(evidence), candidates=len(row['evidence']),
                           state='supported' if evidence else 'unconfirmed', evidence=evidence))
    return result


def stability(takes, outputs, limit):
    points = defaultdict(dict)
    for row in outputs['tune']:
        if row['deviation'] is not None:
            points[row['id']]['基线'] = row['deviation']
    for key, _, out in takes:
        for row in out['tune']:
            if row['deviation'] is not None:
                points[row['id']][f'复测/{key}'] = row['deviation']
    return [dict(id=key, count=len(vals), spread=(spread := max(vals.values())-min(vals.values())) if len(vals) > 1 else None,
                 state='pending' if len(vals) < 2 else 'stable' if spread <= limit else 'drift', evidence=vals)
            for key, vals in sorted(points.items())]
