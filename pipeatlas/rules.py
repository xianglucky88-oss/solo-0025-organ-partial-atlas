from .allocation import assign_peaks


def decide(model, facts):
    graph = facts['graph']
    assigned, allocation, conflicts = assign_peaks(graph, facts['candidate'])
    coverage, tuning, revisit = [], [], []
    for pipe in model['pipes']:
        key=pipe['id']
        if key not in model['opened']:
            continue
        known = [r for r in allocation if r['pipe']==key]
        fundamental = next((r for r in known if r['harmonic']==1),None)
        maybe = [peak for peak,edges in graph.items() if any(edge[:2]==(key,1) for edge in edges) and peak not in assigned]
        state = 'covered' if fundamental else 'ambiguous' if maybe else 'overtone' if known else 'missing'
        coverage.append(dict(id=key,state=state,count=len(known),evidence=[r['id'] for r in known]+maybe))
        deviation = fundamental['deviation'] if fundamental else None
        verdict = 'pending' if deviation is None else 'ready' if abs(deviation)<=model['passband'] else 'adjust'
        tuning.append(dict(id=key,state=verdict,deviation=deviation,evidence=fundamental['id'] if fundamental else maybe))
        if verdict != 'ready':
            rank = {'missing':1,'ambiguous':2,'overtone':3,'covered':4}[state]
            revisit.append(dict(id=key,state=state,rank=rank,evidence=maybe))
    return dict({key:facts[key] for key in ('target','candidate')}, assign=allocation, conflict=conflicts,
            coverage=coverage, tune=tuning, revisit=sorted(revisit,key=lambda x:(x['rank'],x['id'])))
