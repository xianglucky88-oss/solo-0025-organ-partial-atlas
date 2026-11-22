from collections import Counter


def assign_peaks(graph, candidates):
    assigned = {}
    # Both endpoints must be unique; a lower numerical residual cannot resolve competition.
    votes = Counter(edge[:2] for edges in graph.values() for edge in edges)
    for peak,edges in graph.items():
        if len(edges)==1 and votes[edges[0][:2]]==1:
            assigned[peak] = edges[0]
    allocation, conflicts = [], []
    for row in candidates:
        peak=row['id']
        chosen=assigned.get(peak)
        allocation.append(dict(id=peak,hz=row['hz'],pipe=chosen[0] if chosen else None,
                               harmonic=chosen[1] if chosen else None,deviation=chosen[2] if chosen else None,
                               state='assigned' if chosen else 'ambiguous' if graph[peak] else row['state']))
        if graph[peak] and not chosen:
            contested = [f'{pipe}/{n}' for pipe,n,_ in graph[peak] if votes[(pipe,n)]>1]
            conflicts.append(dict(id=peak,state='contested' if contested else 'ambiguous',evidence=row['evidence'],contested=contested))
    return assigned, allocation, conflicts
