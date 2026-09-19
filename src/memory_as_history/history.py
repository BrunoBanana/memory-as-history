"""Explicit chronology and bounded evidence expansion; no inferred authority."""
from datetime import datetime, timezone
import re


def event_time(value, field='event_at'):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value
    ):
        raise ValueError(f'{field} must be a timezone-aware ISO timestamp, or null when unknown')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f'{field} must be a valid timezone-aware ISO timestamp') from exc


def context_values(event_at, session_id, session_position):
    at = event_time(event_at)
    if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
        raise ValueError('session_id must be nonempty text or null')
    if session_position is not None and (type(session_position) is not int or
            not 0 <= session_position < 2**63 or session_id is None):
        raise ValueError('session_position must be a nonnegative integer with a session_id')
    return {'event_at': at, 'session_id': session_id, 'session_position': session_position}


def time_range(since, until):
    since, until = event_time(since, 'since'), event_time(until, 'until')
    if since is not None and until is not None and since > until:
        raise ValueError('since must be at or before until')
    return since, until


def within_time(row, since, until):
    at = row.get('event_at')
    return ((since is None and until is None) or
            (at is not None and (since is None or at >= since) and (until is None or at <= until)))


def check_limit(limit):
    if type(limit) is not int or limit < 0:
        raise ValueError('limit must be a nonnegative integer')


def expand_ranked(rows, links, budget, expand):
    """Keep three-fifths base seeds, expand one hop, then fill base rank.

    Only rows from the final eligibility snapshot can be endpoints or bridges.
    Paths describe retrieval decisions, not factual or causal verification.
    """
    if not budget or not rows:
        return [], []
    if expand == 'none':
        return rows[:budget], []
    rank = {row['id']: i for i, row in enumerate(rows)}
    lookup = {row['id']: row for row in rows}
    neighbors = {mid: [] for mid in rank}
    if expand in ('links', 'both'):
        for edge in links:
            a, b = edge['from_id'], edge['to_id']
            if a in rank and b in rank:
                for source, target in ((a, b), (b, a)):
                    neighbors[source].append((0, rank[target], target,
                        {'kind': 'explicit_link', 'link_id': edge['id'],
                         'from_id': a, 'to_id': b, 'relation': edge['relation']}))
    if expand in ('session', 'both'):
        positions = {(r['session_id'], r['session_position']): r['id'] for r in rows
                     if r.get('session_id') is not None and r.get('session_position') is not None}
        for row in rows:
            position = row.get('session_position')
            if row.get('session_id') is None or position is None:
                continue
            for offset in (-1, 1):
                target = positions.get((row['session_id'], position + offset))
                if target is not None:
                    neighbors[row['id']].append((1, rank[target], target,
                        {'kind': 'session_neighbor', 'offset': offset, 'session_id': row['session_id']}))
    seeds = rows[:(3 * budget + 4) // 5]
    selected, seen, paths = list(seeds), {r['id'] for r in seeds}, []
    # Round-robin over original seeds only; a second pass can collect another
    # explicit prerequisite from one seed, but never traverse a newly added row.
    queues = {seed['id']: sorted(neighbors[seed['id']], key=lambda item: item[:3]) for seed in seeds}
    while len(selected) < budget:
        added = False
        for seed in seeds:
            queue = queues[seed['id']]
            while queue and queue[0][2] in seen:
                queue.pop(0)
            if queue and len(selected) < budget:
                _, _, target, path = queue.pop(0)
                selected.append(lookup[target]); seen.add(target)
                paths.append({'seed_id': seed['id'], 'memory_id': target, **path})
                added = True
        if not added:
            break
    for row in rows:
        if len(selected) >= budget:
            break
        if row['id'] not in seen:
            selected.append(row); seen.add(row['id'])
    return selected, paths
