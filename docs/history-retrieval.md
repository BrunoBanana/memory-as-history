# Chronology and linked evidence

`timeline` and `search_history` use explicitly recorded event/session context.
Existing `recall` and `search` retain their ranking and priority rules.

```python
from memory_as_history.storage import Store

store = Store('memory.db')
try:
    old = store.remember('Comet deadline: March 4.',
                         event_at='2025-03-01T09:00:00Z', session_id='comet:meeting-1', session_position=0)
    new = store.remember('Comet deadline changed to April 10.',
                         event_at='2025-03-10T09:00:00Z', session_id='comet:meeting-2', session_position=0)
    cause = store.remember('Review found a missing signature.',
                           session_id='comet:meeting-2', session_position=1)
    store.link_memories(new.id, old.id, 'updates', 'Revised notice explicitly references the original')
    store.link_memories(cause.id, new.id, 'explains', 'Review explicitly gives the reason for the change')
    evidence = store.search_history('Comet deadline change and reason', mode='lexical', expand='links', limit=5)
    chronology = store.timeline(since='2025-03-01T00:00:00Z', until='2025-03-31T23:59:59Z')
finally:
    store.close()
```

All these methods are also MCP tools. `mode='lexical'` needs no model; semantic
and hybrid modes use the existing [explicit local encoder setup](semantic-search.md).

## Metadata and corrections

- `event_at` is the caller's asserted occurrence timestamp, normalized to UTC.
  Supply full ISO date/time with a timezone; date-only, naive or natural-language
  inputs are rejected. Unknown time is null. `created_at` remains capture time.
  A conversation/session date does not establish when a narrated event occurred.
- `session_id` scopes one conversation session. Include a conversation prefix
  when importing multiple conversations; this label is not source identity.
- `session_position` is a nonnegative integer and requires a session ID. A
  position belongs to one memory, including after that memory is forgotten.
- `set_history_context(memory_id, reason, event_at?, session_id?, session_position?)`
  replaces the **entire context**; omitted/null fields clear. It audits before
  and after values atomically. Changes invalidate dependent narratives until
  explicit review; they do not rewrite memory content or prior audits.

Migration adds three nullable columns, a retractable `memory_links` table and
uniqueness indexes in the existing schema transaction. Existing rows retain
unknown context; there is no guessed backfill or destructive rewrite.

## Asserted relationships

`link_memories(from_id, to_id, relation, reason)` accepts `related`, `updates`,
or `explains`. Direction and reason remain inspectable. Both endpoints must be
active and distinct; repeated active triples return the existing link. `updates`
does not delete, supersede or automatically prefer the earlier/later fact.
`explains` records what the caller asserts, not independently verified causality.
Links cannot corroborate, promote, pin or canonize a memory.

`unlink_memories(link_id, reason)` retires a link with an audit, retaining its
original reason and recording retirement time/reason. Repeating retirement is a
no-op. `memory_links(memory_id, include_retired=False)` is explicit historical
inspection and can show links to forgotten endpoints. Default search expansion
only traverses currently eligible endpoints and never uses forgotten records
as intermediate context.

## Views and budgets

`timeline(frame?, session_id?, since?, until?, limit=50)` returns active records
in ascending explicit event time. Unknown event times appear last, ordered by
capture time/ID only within that unknown group. Inclusive `since`/`until` bounds
exclude unknown event times. `unknown_event_times` counts unknowns within the
requested frame/session before time filtering; `eligible_count` is before limit.
This inspection view has a strict limit, including anchors/canon.

`search_history(query, limit=10, frame?, mode='hybrid', since?, until?, expand='both')`
keeps the usual global anchor/canon priorities. Frame and event-time bounds apply
**only to ordinary memories**; priority sections and narrative/conflict sections
retain the existing recall contract. It is not an as-of snapshot of database
state, does not automatically parse dates from questions, and does not resolve
which conflicting account is true.

Expansion choices are `none`, `links`, `session`, `both`. `none` isolates filtered
base ranking. The other modes reserve `ceil(3*ordinary_budget/5)` base seeds, then
add neighbors round-robin over those original seeds, filling any remaining slots
from base rank. Each traversal is one hop. Explicit links precede session neighbors;
within each type original rank breaks ties. Session neighbors are exactly +/-1
position within the same session. Adjacent turns may be irrelevant: expansion is
an opt-in retrieval heuristic, not evidence that the turns support each other.

The shared item budget is unchanged: anchors can exceed it; canon uses the next
slots; ordinary seeds/expansions share what remains. No hidden extra evidence is
returned outside that budget. `retrieval.evidence_paths` identifies the selected
expansion's seed, link/direction/relation or session offset, without extra text.

A final transactional snapshot rechecks time/frame/forgetting, link retirement,
current context, priorities and narrative review after encoder work. Only current
ordinary candidates can be seeds or endpoints. New/changed content falls back to
lexical rank. Encoding never holds the Store or SQLite writer lock. As with any
read, later mutations require another retrieval.

## Evaluation and reproduction

```sh
python -m memory_as_history.benchmarks history --split dev --output history-dev.json
python -m memory_as_history.benchmarks history --split test --semantic --output history-test.json
python -m memory_as_history.benchmarks locomo --data .benchmark-data/locomo10.json \
  --semantic --history --output history-external.json
python scripts/history_acceptance.py --semantic --output history-mcp.json
```

See the [diagnosis](benchmarks/2026-09-19-history-diagnosis.md) and
[results](benchmarks/2026-09-19-history-results.md). The challenge has explicit
fixture links and ranges, correlated translations, and author-visible topic
splits. It is not a blind independent dataset. External LoCoMo runs receive
session order only, with no gold-derived links or event-time windows. Neither
track measures autonomous tool choice, automatic date/relation extraction,
answer generation or correct abstention. All regressions remain in reports.
