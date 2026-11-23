"""整组归属：在候选二部图上求最大可同时归属，并枚举所有最优解释的共性。

候选仍由温标、幅值门槛、开管名单和音分容差生成，本模块只负责解算：
每个峰与每个槽最多使用一次，峰允许在最优解释中不被指认。判定完全基于
“哪些边能进入某个最大基数匹配”，不挑选最小误差或字典序方案。
"""
from collections import defaultdict


def _maximum_matching(peaks, adjacency):
    """Kuhn 增广路求峰→槽的最大基数匹配，返回 {peak: slot}。"""
    match_slot = {}

    def augment(peak, seen):
        for slot in adjacency[peak]:
            if slot in seen:
                continue
            seen.add(slot)
            holder = match_slot.get(slot)
            if holder is None or augment(holder, seen):
                match_slot[slot] = peak
                return True
        return False

    for peak in peaks:
        augment(peak, set())
    return {peak: slot for slot, peak in match_slot.items()}


def _components(peaks, adjacency):
    """按候选边把峰与槽划分连通组，返回 [(峰集合, 槽集合)]。"""
    slot_peaks = defaultdict(list)
    for peak in peaks:
        for slot in adjacency[peak]:
            slot_peaks[slot].append(peak)
    remaining = set(peaks)
    groups = []
    while remaining:
        seed = min(remaining)
        stack = [seed]
        group_peaks, group_slots = set(), set()
        while stack:
            peak = stack.pop()
            if peak in group_peaks:
                continue
            group_peaks.add(peak)
            for slot in adjacency[peak]:
                if slot in group_slots:
                    continue
                group_slots.add(slot)
                stack.extend(other for other in slot_peaks[slot] if other not in group_peaks)
        remaining.difference_update(group_peaks)
        groups.append((group_peaks, group_slots))
    return groups


def _reachable_from(adj, starts):
    seen = set(starts)
    stack = list(starts)
    while stack:
        node = stack.pop()
        for nxt in adj.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _reverse(adj, nodes):
    flipped = defaultdict(list)
    for node in nodes:
        for nxt in adj.get(node, ()):
            flipped[nxt].append(node)
    return flipped


def _strong_components(adj, nodes):
    """迭代 Tarjan：返回每个节点所属强连通分量编号（编号无业务含义）。"""
    index = {}
    low = {}
    on_stack = set()
    stack = []
    component = {}
    counter = 0
    serial = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, edge_pos = work[-1]
            if edge_pos == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbors = adj.get(node, ())
            if edge_pos < len(neighbors):
                work[-1] = (node, edge_pos + 1)
                nxt = neighbors[edge_pos]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                if low[node] == index[node]:
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component[member] = serial
                        if member == node:
                            break
                    serial += 1
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return component


def _edge_id(slot):
    pipe, harmonic = slot
    return f'{pipe}/{harmonic}'


def analyze(graph, hz_by_peak):
    """对候选图做整组解算。

    返回 (peak_rows, cluster_rows, allowed_edges)：
    - peak_rows 按峰报告可占槽、可否未归、是否唯一必然槽与连通组规模；
    - cluster_rows 按连通组报告最大归属峰数、最少未归峰数与必死候选边；
    - allowed_edges 为 {peak: {(pipe, harmonic)}}，只含能进入某个最优解释的边。
    """
    peaks = list(graph.keys())
    adjacency = {peak: [edge[:2] for edge in edges] for peak, edges in graph.items()}
    matching = _maximum_matching(peaks, adjacency)

    def left(peak):
        return ('L', peak)

    def right(slot):
        return ('R', slot)

    # 有向图：未匹配边 峰→槽，匹配边 槽→峰。节点加左右命名空间避免身份混淆。
    directed = defaultdict(list)
    for peak in peaks:
        for slot in adjacency[peak]:
            if matching.get(peak) == slot:
                directed[right(slot)].append(left(peak))
            else:
                directed[left(peak)].append(right(slot))
    nodes = [left(peak) for peak in peaks]
    nodes += [right(slot) for slot in {slot for slots in adjacency.values() for slot in slots}]
    for node in nodes:
        directed.setdefault(node, [])
    scc = _strong_components(directed, nodes)
    free_left = [left(peak) for peak in peaks if peak not in matching]
    matched_slots = set(matching.values())
    all_slots = {slot for slots in adjacency.values() for slot in slots}
    free_right = [right(slot) for slot in all_slots if slot not in matched_slots]
    # 从自由峰正向可达（增广路的前段），以及沿反向图从自由槽可达（增广路的后段）。
    forward = _reachable_from(directed, free_left)
    backward = _reachable_from(_reverse(directed, nodes), free_right)

    allowed, can_skip = {}, set()
    for peak in peaks:
        kept = set()
        for slot in adjacency[peak]:
            if matching.get(peak) == slot:
                # 当前最大匹配本身就是一个最优解释，匹配边必然可用。
                edge_kept = True
            else:
                # 未匹配边能进入某个最大匹配，当且仅当：
                # 1) 位于交替环上（两端同属一个强连通分量）；
                # 2) 位于自由峰出发的增广路前段（峰被自由峰正向可达）；
                # 3) 位于通向自由槽的增广路后段（槽沿反向图可到自由槽）。
                edge_kept = (scc[left(peak)] == scc[right(slot)]
                             or left(peak) in forward or right(slot) in backward)
            if edge_kept:
                kept.add(slot)
        allowed[peak] = kept
        # 峰能在某个最大匹配中不指认，当且仅当它本身是自由峰，或可从
        # 自由峰沿有向交替边到达（处在某条增广路的前段上）。
        if left(peak) in forward:
            can_skip.add(peak)

    peak_rows, cluster_rows = [], []
    for group_peaks, group_slots in _components(peaks, adjacency):
        if not group_slots:
            continue
        seed = min(group_peaks)
        matched = sum(1 for peak in group_peaks if peak in matching)
        deficit = len(group_peaks) - matched
        blocked = sorted(f'{peak}→{_edge_id(slot)}' for peak in group_peaks
                         for slot in set(adjacency[peak]) - allowed[peak])
        has_flexible = any(peak in can_skip or len(allowed[peak]) != 1 for peak in group_peaks)
        if has_flexible:
            state = 'oversubscribed' if deficit > 0 else 'unresolved'
        else:
            state = 'resolved'
        cluster_rows.append(dict(
            id=seed, count=len(group_peaks), slots=len(group_slots), state=state,
            matched=matched, deficit=deficit, dead_edges=blocked,
            evidence={'peaks': sorted(group_peaks),
                      'slots': [_edge_id(slot) for slot in sorted(group_slots)]}))
        for peak in sorted(group_peaks):
            forced_slot = None
            if peak not in can_skip and len(allowed[peak]) == 1:
                forced_slot = _edge_id(next(iter(allowed[peak])))
            status = 'forced' if forced_slot else 'flexible' if allowed[peak] else 'idle'
            peak_rows.append(dict(
                id=peak, hz=hz_by_peak[peak], state=status, component=seed,
                slots=[_edge_id(slot) for slot in sorted(allowed[peak])],
                forced=forced_slot, may_unassign=peak in can_skip,
                dead_edges=sorted(_edge_id(slot) for slot in set(adjacency[peak]) - allowed[peak])))
    # 无候选边的峰各自成空组，也要在峰级报告中留账。
    for peak in peaks:
        if not adjacency[peak]:
            peak_rows.append(dict(
                id=peak, hz=hz_by_peak[peak], state='idle', component=peak,
                slots=[], forced=None, may_unassign=True, dead_edges=[]))
    peak_rows.sort(key=lambda row: row['id'])
    cluster_rows.sort(key=lambda row: row['id'])
    return peak_rows, cluster_rows, allowed
