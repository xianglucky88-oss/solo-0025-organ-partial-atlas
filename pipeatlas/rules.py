from . import certificate
from .allocation import assign_peaks

RANK = {'missing': 1, 'blind': 1, 'muted': 1, 'unseen': 1, 'ambiguous': 2, 'overtone': 3, 'covered': 4}


def decide(model, facts):
    graph = facts['graph']
    assigned, allocation, conflicts = assign_peaks(graph, facts['candidate'])
    coverage, tuning, revisit = [], [], []
    certified = model.get('certificate')
    for pipe in model['pipes']:
        key=pipe['id']
        if key not in model['opened']:
            continue
        known = [r for r in allocation if r['pipe']==key]
        fundamental = next((r for r in known if r['harmonic']==1),None)
        maybe = [peak for peak,edges in graph.items() if any(edge[:2]==(key,1) for edge in edges) and peak not in assigned]
        gaps = []
        muted = []
        lo, hi = None, None
        # Certified-not-seen: opened, no trusted fundamental, no pending compatible peak, and
        # the whole tolerance band around the fundamental is certified clean. A partially
        # blind band is not proof of absence.
        unseen = False
        if certified is not None:
            target = pipe['base']*model['ratio']
            lo, hi = certificate.tolerance_band(target, model['tolerance'])
            gaps = certificate.uncovered(certified, lo, hi)
            muted = certificate.polluted_overlap(certified, lo, hi)
            unseen = not fundamental and not maybe and not gaps
        if fundamental:
            state = 'covered'
        elif maybe:
            state = 'ambiguous'
        elif certified is not None:
            # Distinguish a band that never covered the fundamental (blind) from one that
            # claimed it but was notched or clipped there (muted): they are different
            # failures and must not be merged in the missing ledger.
            if unseen:
                state = 'unseen'
            elif certificate.muted_by_pollution(certified, lo, hi):
                state = 'muted'
            else:
                state = 'overtone' if known else 'blind'
        else:
            state = 'overtone' if known else 'missing'
        evidence = [r['id'] for r in known]+maybe
        if state == 'unseen':
            evidence = {'bands': certified['effective'], 'gaps': gaps}
        elif state == 'muted':
            evidence = {'seen': [r['id'] for r in known], 'polluted': muted, 'gaps': gaps}
        elif state == 'blind':
            evidence = {'seen': [r['id'] for r in known], 'gaps': gaps}
        coverage.append(dict(id=key,state=state,count=len(known),evidence=evidence))
        deviation = fundamental['deviation'] if fundamental else None
        verdict = 'pending' if deviation is None else 'ready' if abs(deviation)<=model['passband'] else 'adjust'
        tuning.append(dict(id=key,state=verdict,deviation=deviation,evidence=fundamental['id'] if fundamental else maybe))
        if verdict != 'ready':
            revisit.append(dict(id=key,state=state,rank=RANK[state],evidence=maybe))
    return dict({key:facts[key] for key in ('target','candidate')}, assign=allocation, conflict=conflicts,
            coverage=coverage, tune=tuning, revisit=sorted(revisit,key=lambda x:(x['rank'],x['id'])))
