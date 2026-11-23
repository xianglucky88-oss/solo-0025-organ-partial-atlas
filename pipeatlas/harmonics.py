from math import log2

from . import certificate


def cents(actual, target):
    return 1200 * log2(actual / target)


def evaluate(model):
    certified = model.get('certificate')
    targets = [dict(id=p['id'],hz=p['base']*model['ratio'],state='open' if p['id'] in model['opened'] else 'closed') for p in model['pipes']]
    slots = [(p['id'],n,p['base']*model['ratio']*n) for p in model['pipes'] if p['id'] in model['opened'] for n in range(1,p['maximum']+1)]
    candidates, graph = [], {}
    for p in model['peaks']:
        band = certificate.band_of(certified, p['hz'])
        # Certified-and-unpolluted is a precondition for taking part in this session's
        # candidates. Every other observation stays as raw evidence (hz, amplitude) but
        # contributes no edges, so it cannot shape assignment or coverage.
        usable = p['amplitude'] >= model['floor'] and (certified is None or band == 'clean')
        edges = []
        if usable:
            for pipe,n,target in slots:
                deviation = cents(p['hz'],target)
                if abs(deviation) <= model['tolerance']:
                    edges.append((pipe,n,deviation))
        graph[p['id']] = edges
        row = dict(id=p['id'], hz=p['hz'],amplitude=p['amplitude'],count=len(edges),
                   evidence=[f'{pipe}/{n}' for pipe,n,_ in edges],
                   state=('weak' if p['amplitude']<model['floor']
                          else 'polluted' if band == 'polluted' else 'unlicensed' if band == 'blind'
                          else 'candidate' if edges else 'outside'))
        candidates.append(row)
    return dict(target=targets,candidate=candidates,graph=graph)
