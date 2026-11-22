from math import log2


def cents(actual, target):
    return 1200 * log2(actual / target)


def evaluate(model):
    targets = [dict(id=p['id'],hz=p['base']*model['ratio'],state='open' if p['id'] in model['opened'] else 'closed') for p in model['pipes']]
    slots = [(p['id'],n,p['base']*model['ratio']*n) for p in model['pipes'] if p['id'] in model['opened'] for n in range(1,p['maximum']+1)]
    candidates, graph = [], {}
    for p in model['peaks']:
        edges = []
        if p['amplitude'] >= model['floor']:
            for pipe,n,target in slots:
                deviation = cents(p['hz'],target)
                if abs(deviation) <= model['tolerance']:
                    edges.append((pipe,n,deviation))
        graph[p['id']] = edges
        candidates.append(dict(id=p['id'], hz=p['hz'],amplitude=p['amplitude'],count=len(edges),
                         evidence=[f'{pipe}/{n}' for pipe,n,_ in edges],
                         state='weak' if p['amplitude']<model['floor'] else 'candidate' if edges else 'outside'))
    return dict(target=targets,candidate=candidates,graph=graph)
