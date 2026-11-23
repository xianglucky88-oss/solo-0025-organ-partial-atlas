from collections import Counter

from . import matching
from .allocation import assign_peaks


def decide(model, facts):
    graph = facts['graph']
    whole_group = model.get('whole_group', False)
    if whole_group:
        hz_by_peak = {row['id']: row['hz'] for row in facts['candidate']}
        peak_rows, cluster_rows, allowed = matching.analyze(graph, hz_by_peak)
        # 只有每个最优解释都指到同一槽的峰才作为可信实测。
        assigned = {}
        for row in peak_rows:
            if row['state'] == 'forced':
                pipe, harmonic = row['forced'].split('/')
                edge = next(edge for edge in graph[row['id']] if edge[:2] == (pipe, int(harmonic)))
                assigned[row['id']] = edge
        # 争用只统计仍可进入最优解释的边，被否决的候选不参与槽位竞争。
        claims = Counter(slot for other in graph if other not in assigned for slot in allowed[other])
    else:
        assigned, allocation, conflicts = assign_peaks(graph, facts['candidate'])
        allowed = None
        claims = None
    allocation, conflicts = [], []
    for row in facts['candidate']:
        peak = row['id']
        chosen = assigned.get(peak)
        allocation.append(dict(id=peak, hz=row['hz'], pipe=chosen[0] if chosen else None,
                               harmonic=chosen[1] if chosen else None, deviation=chosen[2] if chosen else None,
                               state='assigned' if chosen else 'ambiguous' if graph[peak] else row['state']))
        if graph[peak] and not chosen:
            if whole_group:
                contested = [f'{pipe}/{n}' for pipe, n, _ in graph[peak]
                             if (pipe, n) in allowed[peak] and claims[(pipe, n)] > 1]
            else:
                votes = Counter(edge[:2] for edges in graph.values() for edge in edges)
                contested = [f'{pipe}/{n}' for pipe, n, _ in graph[peak] if votes[(pipe, n)] > 1]
            conflicts.append(dict(id=peak, state='contested' if contested else 'ambiguous',
                                  evidence=row['evidence'], contested=contested))
    coverage, tuning, revisit = [], [], []
    for pipe in model['pipes']:
        key = pipe['id']
        if key not in model['opened']:
            continue
        known = [r for r in allocation if r['pipe'] == key]
        fundamental = next((r for r in known if r['harmonic'] == 1), None)
        if whole_group:
            maybe = [peak for peak, edges in graph.items()
                     if any(edge[:2] == (key, 1) and edge[:2] in allowed[peak] for edge in edges)
                     and peak not in assigned]
        else:
            maybe = [peak for peak, edges in graph.items()
                     if any(edge[:2] == (key, 1) for edge in edges) and peak not in assigned]
        state = 'covered' if fundamental else 'ambiguous' if maybe else 'overtone' if known else 'missing'
        coverage.append(dict(id=key, state=state, count=len(known), evidence=[r['id'] for r in known]+maybe))
        deviation = fundamental['deviation'] if fundamental else None
        verdict = 'pending' if deviation is None else 'ready' if abs(deviation) <= model['passband'] else 'adjust'
        tuning.append(dict(id=key, state=verdict, deviation=deviation,
                           evidence=fundamental['id'] if fundamental else maybe))
        if verdict != 'ready':
            rank = {'missing': 1, 'ambiguous': 2, 'overtone': 3, 'covered': 4}[state]
            revisit.append(dict(id=key, state=state, rank=rank, evidence=maybe))
    result = dict({key: facts[key] for key in ('target', 'candidate')}, assign=allocation, conflict=conflicts,
                  coverage=coverage, tune=tuning, revisit=sorted(revisit, key=lambda x: (x['rank'], x['id'])))
    if whole_group:
        result['ensemble'] = peak_rows
        facts['ensemble_clusters'] = cluster_rows
    return result
