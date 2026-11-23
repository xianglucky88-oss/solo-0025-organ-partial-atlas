"""Effective-dated seat/entity binding and cross-take entity alignment.

A *seat* is a named position in the instrument; an *entity* is one physical
pipe.  Repair registrations bind a seat to one entity over a half-open
interval ``[start, end)`` and carry the maintenance credential.  A take whose
time equals a replacement boundary belongs to the entity whose interval starts
at that second, because the earlier interval is already closed.

Per-take verdicts stay seat-relative: they were produced against the seat
target that was in force when the take was recorded, and they are preserved
verbatim.  Cross-take alignment regroups trustworthy observations by entity and
re-expresses the measured peak against the entity's own pre-selected reference
frequency and temperature.  Deviations measured against two different seat
targets are never subtracted from one another.

Whenever identity or trust is missing -- no timestamp, no registration, a
contradictory maintenance credential, or no trustworthy fundamental -- the
observation is left unaligned instead of being guessed onto an entity.
"""

from collections import defaultdict
from math import sqrt

from .contract import ContractError, ident, invariant, num, obj, records
from .harmonics import cents

INFINITY = float('inf')


def _overlaps(first, second):
    """Half-open interval comparison: touching endpoints do not overlap."""
    left = first['end'] if first['end'] is not None else INFINITY
    return second['start'] < left


def _read_registry(review, model):
    registry = obj(review.get('registry'), '维修登记')
    entities = []
    for item in records(registry.get('entities'), '音管实体', 60, 1):
        entities.append(dict(id=item['id'],
                             base=num(item.get('base'), '实体基准频率', 16, 12000),
                             reference=num(item.get('reference'), '实体基准温度', 0, 40)))
    seats = {p['id'] for p in model['pipes']}
    known = {entity['id'] for entity in entities}
    links = []
    for item in records(registry.get('links'), '席位对应', 600, 0):
        seat = ident(item.get('seat'), '对应席位')
        entity = ident(item.get('entity'), '对应实体')
        invariant(seat in seats, f'席位对应引用了未知席位：{seat}')
        invariant(entity in known, f'席位对应引用了未知实体：{entity}')
        start = num(item.get('start'), '生效起点', 0, 1e9)
        end = item.get('end')
        if end is not None:
            end = num(end, '生效终点', 0, 1e9)
            invariant(end > start, '生效终点必须大于生效起点')
        links.append(dict(id=item['id'], seat=seat, entity=entity, start=start, end=end,
                          credential=ident(item.get('credential'), '维修凭据')))
    # At one instant a seat holds at most one entity and an entity sits in at
    # most one seat.  Half-open intervals make a hand-over at the same second
    # legal: the earlier interval ends exactly where the later one begins.
    for field, label in (('seat', '席位'), ('entity', '实体')):
        buckets = defaultdict(list)
        for link in links:
            buckets[link[field]].append(link)
        for items in buckets.values():
            items.sort(key=lambda link: (link['start'], link['end'] if link['end'] is not None else INFINITY))
            for first, second in zip(items, items[1:]):
                if _overlaps(first, second):
                    raise ContractError(f'同一时刻{label}只能有一个对应关系：{first["id"]} 与 {second["id"]} 重叠')
    owners = defaultdict(list)
    for link in links:
        owners[link['credential']].append(link)
    return entities, links, owners


def _active(links, at, **match):
    return [link for link in links
            if link['start'] <= at < (INFINITY if link['end'] is None else link['end'])
            and all(link[key] == value for key, value in match.items())]


def _credential_conflict(link, owners):
    """One maintenance record registers one seat's chain of relations.

    A single credential may legitimately document both ends of a replacement on
    one seat.  Reusing it for another seat makes the paperwork contradictory:
    two different positions would claim the same repair.
    """
    return any(other['seat'] != link['seat'] for other in owners.get(link['credential'], []))


def _resolve(links, owners, seat, at):
    """Resolve one seat/time to a single entity or to an explicit refusal.

    ``conflict`` is reachable even though intervals may not overlap: a single
    maintenance credential may not register relations in two different seats.
    """
    if at is None:
        return dict(state='untimed', entity=None, link=None, credential=None, links=[])
    here = _active(links, at, seat=seat)
    if not here:
        return dict(state='unbound', entity=None, link=None, credential=None, links=[])
    if len(here) > 1:
        return dict(state='conflict', entity=None, link=None, credential=None,
                    links=sorted(link['id'] for link in here))
    link = here[0]
    holding = _active(links, at, entity=link['entity'])
    if len(holding) != 1 or _credential_conflict(link, owners):
        related = {item['id'] for item in here + holding}
        related.update(item['id'] for item in owners.get(link['credential'], []))
        return dict(state='conflict', entity=None, link=None, credential=None,
                    links=sorted(related))
    return dict(state='resolved', entity=link['entity'], link=link['id'],
                credential=link['credential'], links=[link['id']])


def _peaks(rows):
    return {row['id']: row['hz'] for row in rows}


def _observation(take, at, temperature, row, hz, order):
    return dict(take=take, at=at, temperature=temperature, seat=row['id'], hz=hz,
                raw=dict(state=row['state'], deviation=row['deviation'], evidence=row['evidence']),
                trusted=row['deviation'] is not None, order=order)


def _observations(model, outputs, takes):
    """Collect the preserved seat-relative verdicts, baseline first."""
    result, order = [], 0
    baseline = _peaks(model['peaks'])
    for row in outputs['tune']:
        evidence = row['evidence']
        hz = baseline.get(evidence) if isinstance(evidence, str) else None
        result.append(_observation('基线', model.get('at'), model['temperature'], row, hz, order))
        order += 1
    for key, configuration, out in takes:
        peaks = _peaks(configuration['peaks'])
        for row in out['tune']:
            evidence = row['evidence']
            hz = peaks.get(evidence) if isinstance(evidence, str) else None
            result.append(_observation(key, configuration.get('at'), configuration['temperature'],
                                       row, hz, order))
            order += 1
    return result


def _seat_rows(links, owners, observations):
    grouped = defaultdict(list)
    for observation in observations:
        grouped[observation['seat']].append(observation)
    rows = []
    for seat in sorted(grouped):
        items = sorted(grouped[seat], key=lambda item: (item['at'] is None, item['at'] or 0, item['order']))
        entities, problems, detail = [], [], []
        for observation in items:
            binding = _resolve(links, owners, seat, observation['at'])
            if binding['state'] == 'resolved':
                entities.append(binding['entity'])
            else:
                problems.append(binding['state'])
            detail.append(dict(take=observation['take'], at=observation['at'],
                               entity=binding['entity'], link=binding['link'],
                               credential=binding['credential'], state=binding['state'],
                               links=binding['links'], trusted=observation['trusted'],
                               raw=observation['raw']))
        if 'conflict' in problems:
            state = 'conflict'
        elif 'untimed' in problems:
            state = 'untimed'
        elif 'unbound' in problems:
            state = 'unbound'
        else:
            state = 'swapped' if len(set(entities)) > 1 else 'unchanged'
        resolved = state in ('swapped', 'unchanged')
        rows.append(dict(id=seat, entity=entities[-1] if resolved else None,
                         count=len(items), state=state,
                         evidence=dict(entities=list(dict.fromkeys(entities)), observations=detail)))
    return rows


def _entity_cents(entity, hz, temperature):
    """Re-express a measured frequency against the entity's own reference."""
    target = entity['base'] * sqrt((273.15 + temperature) / (273.15 + entity['reference']))
    return cents(hz, target)


def _order_key(item):
    # Missing timestamps sort last; ties keep the recorded order (baseline first).
    return (item['at'] is None, item['at'] or 0, item['order'])


def _strip(items):
    """Drop the internal ordering hint from the sealed evidence detail."""
    return [{key: value for key, value in item.items() if key != 'order'} for item in items]


def _entity_rows(entities, links, owners, observations):
    index = {entity['id']: entity for entity in entities}
    trusted, excluded = defaultdict(list), defaultdict(list)
    for observation in observations:
        binding = _resolve(links, owners, observation['seat'], observation['at'])
        if binding['state'] != 'resolved':
            # No trustworthy identity: the observation is not guessed onto a pipe.
            continue
        key = binding['entity']
        if not observation['trusted'] or observation['hz'] is None:
            excluded[key].append(dict(take=observation['take'], at=observation['at'],
                                      seat=observation['seat'], order=observation['order'],
                                      reason='untrusted' if not observation['trusted'] else 'unmeasured',
                                      raw=observation['raw']))
            continue
        entity = index[key]
        trusted[key].append(dict(take=observation['take'], at=observation['at'],
                                 seat=observation['seat'], order=observation['order'],
                                 hz=observation['hz'], temperature=observation['temperature'],
                                 cents=_entity_cents(entity, observation['hz'], observation['temperature']),
                                 credential=binding['credential'], raw=observation['raw']))
    rows = []
    for key in [entity['id'] for entity in entities]:
        if key not in trusted and key not in excluded:
            continue
        entity = index[key]
        points = _strip(sorted(trusted[key], key=_order_key))
        left = _strip(sorted(excluded[key], key=_order_key))
        delta = points[-1]['cents'] - points[0]['cents'] if len(points) >= 2 else None
        seats = [point['seat'] for point in points + left]
        if not points:
            state = 'unresolved'
        elif len(points) == 1:
            state = 'pending'
        else:
            state = 'moved' if len({point['seat'] for point in points}) > 1 else 'held'
        rows.append(dict(id=key, seat=(points or left)[-1]['seat'], count=len(points),
                         delta=delta, state=state,
                         evidence=dict(base=entity['base'], reference=entity['reference'],
                                       seats=list(dict.fromkeys(seats)),
                                       observations=points, excluded=left)))
    return rows


def _unaligned_rows(links, owners, observations):
    """List observations that could not be aligned, with the explicit reason."""
    rows = []
    for observation in observations:
        binding = _resolve(links, owners, observation['seat'], observation['at'])
        if binding['state'] == 'resolved' and observation['trusted']:
            continue
        reason = binding['state'] if binding['state'] != 'resolved' else (
            'untrusted' if not observation['trusted'] else 'unmeasured')
        rows.append(dict(id=f"{observation['take']}/{observation['seat']}",
                         seat=observation['seat'], at=observation['at'], state=reason,
                         evidence=dict(entity=binding['entity'], links=binding['links'],
                                       link=binding['link'], credential=binding['credential'],
                                       raw=observation['raw'])))
    return rows


def analyze(review, model, outputs, takes):
    """Build the seat-change view, the entity-migration view and the gap list."""
    entities, links, owners = _read_registry(review, model)
    observations = _observations(model, outputs, takes)
    return dict(swap=_seat_rows(links, owners, observations),
                migration=_entity_rows(entities, links, owners, observations),
                unaligned=_unaligned_rows(links, owners, observations))
