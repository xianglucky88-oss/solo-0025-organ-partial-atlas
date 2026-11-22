from collections import defaultdict


def summarize(model, facts, outputs):
    unresolved = {r['id'] for r in outputs['assign'] if r['state'] == 'ambiguous'}
    slots = defaultdict(set)
    links = {}
    for peak in unresolved:
        links[peak] = {edge[:2] for edge in facts['graph'][peak]}
        for slot in links[peak]:
            slots[slot].add(peak)
    result = []
    while unresolved:
        seed = min(unresolved)
        stack = [seed]
        component, targets = set(), set()
        while stack:
            peak = stack.pop()
            if peak in component:
                continue
            component.add(peak)
            for slot in links[peak] - targets:
                targets.add(slot)
                stack.extend(slots[slot] - component)
        unresolved.difference_update(component)
        result.append(dict(id=seed, count=len(component), slots=len(targets),
                           state='oversubscribed' if len(component) > len(targets) else 'unresolved',
                           evidence={'peaks':sorted(component), 'slots':[f'{p}/{n}' for p,n in sorted(targets)]}))
    return result
