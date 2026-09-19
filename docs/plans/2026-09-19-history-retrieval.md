# Temporal and linked evidence retrieval implementation plan

> Execute task by task in this worktree using the installed executing-plans and single-flow-task-execution skills. The repository has no .agent workflow files. The user approved this scope and end-to-end delivery; no repeated approval checkpoint is needed.

**Goal:** Preserve explicit event chronology and session structure, retrieve related evidence within the same budget, and measure complete evidence coverage without weakening history safeguards.

**Architecture:** Add nullable event/session metadata and audited, retractable caller-asserted links to SQLite. An opt-in search_history API ranks with lexical/semantic/hybrid retrieval and expands one hop, revalidating eligibility after encoding. Existing recall/search stay unchanged. Expose chronology through a separate timeline tool; unknown event time stays unknown.

**Tech stack:** Python 3.10+, SQLite, existing MCP 1.x/2.x and optional pinned local E5.

## Design and tradeoffs

A larger return budget would confound quality comparisons. Automatic LLM relation extraction would add inference cost and unsupported factual judgments. Use explicit metadata/links plus deterministic session neighbors instead; links express caller assertions, never corroboration, causal proof or supersession. Related and chronological records retain their original content and tier. The accepted design is implemented without further confirmation.

- remember adds keyword-only event_at (timezone-aware ISO timestamp normalized UTC), session_id and session_position (nonnegative integer, requires session_id). Session IDs are caller-scoped and must identify one conversation/session, not source identity. No event time inferred from recorded_at/session labels. get/to_dict expose nullable values; migrations preserve legacy text/IDs/audit.
- set_history_context replaces the entire optional context, requires reason and logs before/after atomically; clearing is explicit through null fields. Duplicate session positions are rejected to avoid ambiguous adjacency.
- link_memories(from_id,to_id,relation,reason): directed relations related/updates/explains; reason required, active endpoints, no self links. Repeated active triples return existing record. unlink_memories tombstones a link with reason; history remains inspectable through memory_links. Traversal works in either direction but returned edges preserve direction/type. No priority/provenance/narrative mutation.
- timeline(frame?,session_id?,since?,until?,limit=50): active rows only; inclusive UTC event range, ascending event time then recorded_at/id; unknown timestamps excluded when bounded and otherwise appear last, explicitly counted. Limit is strict; this is a chronology view, not recall's anchor policy.
- search_history(query,limit=10,frame?,mode='hybrid',since?,until?,expand='both'): modes lexical/semantic/hybrid; expand none/links/session/both. Time range applies to ordinary rows only, preserving existing global anchor/canon priorities. Out-of-range/unknown ordinary rows cannot be used as expansion bridges. Returned retrieval metadata exposes excluded count and evidence_paths. One-hop only, no recursive traversal or authoritative inference.
- Fixed selection policy BEFORE holdout/external evaluation: preserve first ceil(3*budget/5) base-ranked ordinary seeds, then add previously unselected neighbors round-robin over original seeds, up to remaining budget, then fill by original rank. For each seed prefer explicit links to +/-1 session-position neighbors, tie by original rank/id. No global promotion based on similarity or links. expand=none equals base ranking under the same ordinary time filter.
- Encoding outside Store/SQLite writer locks; a final transactional snapshot rechecks eligibility, links/context, anchors/canon and narratives. Newly eligible/changed rows fall back to lexical order. No stale bridge IDs/text leak across forgotten/frame changes.

## Task 1 — diagnosis and frozen challenge data

Files: scripts/diagnose_history_retrieval.py, scripts/build_history_retrieval_corpus.py, src/memory_as_history/benchmarks/data/history-retrieval-v1*.json, docs/benchmarks/2026-09-19-history-diagnosis.md.

1. Reproduce 307-test baseline (passed on clean worktree).
2. Analyze every hybrid miss and all 69 regressions against pinned LoCoMo and preserved trace; save IDs and mechanical indicators, not copyrighted conversation text. Distinguish indicators from causal labels.
3. Freeze harder authored bilingual histories with separate topic groups for development/evaluation. Include corrections, causal links, cross-session evidence, undated entries and distracting same-topic records. Gold and case labels never enter ranking. Split is author-visible, not a blind independent dataset.
4. Commit fixtures/manifest/config before any new retrieval scoring. No tuning on evaluation split or LoCoMo. Diagnose zero-overlap, adjacency and cross-session evidence separately.

## Task 2 — metadata, chronology and accountable links (TDD)

Files: src/memory_as_history/storage.py, src/memory_as_history/history.py, tests/test_history_retrieval.py.

Write failures for new API, old schema migration, invalid dates/positions, rollback, duplicate context, direction and retraction, limits/ranges, forgetting/frame filtering and restart. Run targeted pytest and confirm missing feature failures. Implement transactional APIs, then run full suite. Review schema preservation, audit atomicity and minimal changes before committing.

## Task 3 — history search and MCP (TDD)

Files: storage.py, history.py, server.py, tests/test_history_retrieval.py, tests/test_server.py, scripts/history_acceptance.py.

Write failures for fixed expansion/budgets/paths, cross-session explicit links, no frame/forgotten/time bridges, semantic concurrent mutations, source guards and two-process stdio use. Implement snapshots/selection and new MCP tools; preserve literal string compatibility. Verify targeted and full suites. Use real local encoder in separate acceptance in addition to controlled-score tests.

## Task 4 — comparative measurement

Files: benchmarks/history_retrieval.py, benchmarks/retrieval.py, benchmarks/__main__.py, tests/test_benchmarks.py, docs/benchmarks/results/2026-09-19-history-*.

Add model-free challenge and optional real encoder runs with lexical/hybrid, expansion none/links/session/both and chronological filters. Test complete-coverage scoring, unchanged denominator on failures, budget accounting and label isolation. Freeze code before evaluation split/external scoring. External session metadata comes only from original ordered sessions; supply no gold links/event times. Keep all original five baselines unchanged and report history expansion regressions/latency even if quality falls. Never replace default search with an unproven expansion. Compare every baseline selection to prior traces; rerun final artifacts/actual MCP and installed wheel as appropriate.

## Task 5 — review and delivery

Update bilingual docs, protocol/schema contract, benchmark limits and changelog. Run all tests across MCP 1.2.0/current 1.x/2.x, 6 reliability checks, existing mechanisms, 120 episodes/990 assertions, package build and installed-wheel tests. Review specification then implementation, resolve findings, create PR, require nine checks on exact head, merge and verify main. No PyPI publication.

## Initial diagnosis (23f0955 baseline)

405 multi-evidence questions; 370 incomplete; 326 span sessions. 1,372 missing evidence turns; 239 within one session-position of a selected turn, 445 within three; 20 have zero lexical query overlap. These are descriptive upper-bound opportunities, not achievable gains or verified root causes. All 69 regressions retained.

Pre-scoring review: a seed can need both its previous version and its explanation. Expansion now makes additional round-robin passes over original seeds when slots remain; still one-hop, fixed three-fifths seed quota. A deterministic regression covers two linked prerequisites. Corrected the authored explains edge direction before any scoring; gold/text/splits unchanged. Event-context correction also invalidates dependent narratives atomically.


## Source validation and measured outcome

- Fixed runner 9223352. Three MCP environments (1.2.0, 1.30.0, 2.2.0) each pass
  349 tests after final transport validation. 6/6 reliability; 120 episodes/990
  assertions; existing usefulness/poisoning checks pass. Source actual-model MCP
  acceptance passes again after strict integer transport validation.
- Spec review checked metadata/time distinction, nullable migration, atomic
  audits, directed links/retraction, global priority budgets, one-hop bounds and
  fresh concurrency snapshots. Quality review added migration rollback,
  competing session-position claims, cycle/duplicate-target and empty-time tests.
  Transport review reproduced boolean coercion in three failing tests and fixed
  it with StrictInt fields on the new interfaces; all three SDK versions pass.
- Authored dev/evaluation: hybrid complete coverage 0% -> 66.67%, identical
  aggregates across correlated templates. English 100%, Chinese 33.33% after
  expansion. Explicit fixture structure is supplied, not inferred.
- External session expansion: overall 51.8971% -> 52.0743%; multi-evidence
  recall 26.0417% -> 23.9938%; complete multi-evidence stays 35/405. 94 wins /
  1326 ties / 107 losses. All 7635 original baseline rows match prior traces.
  No evaluation-driven parameter changes; ordinary search remains unchanged.
- Independent annotation of pronoun/temporal root causes and a blind external
  history holdout are not claimed. Packaging and PR/main integration follow.

Installed-wheel validation: fresh environment outside checkout passes 349/349,
actual-model MCP acceptance and all 240 evaluation-split selections/quality rows
match source. Archive checks confirm code bytes and no model/external dataset.
The full external installed-wheel replay also matches all 10,689 selected-ID,
quality and content-budget rows, with matching scorer hashes. PR #8 is submitted;
merge/main completion is verified against the exact GitHub commits.
