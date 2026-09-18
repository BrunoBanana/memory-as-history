# Changelog

All notable changes to this project are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

1.2.0 release candidate (`1.2.0rc1`), not yet published. Additive narrative schema migration; existing text and history preserved.

### Changed
- MCP string arguments preserve literal text across supported SDK versions,
  including numeric/exponent-shaped legacy IDs and optional fields containing
  `"null"` or JSON-shaped text. Structured list arguments retain SDK parsing.
  Nine real stdio regressions bring the full suite to 307 tests.
- Sensitive canon and narrative routes enforce independent corroboration. A
  sensitive synthesis requires supported evidence for every linked memory.
  Retroactive flags decommission unsupported canon memberships atomically.
- New narrative links must be existing active sources; unlinked nonsensitive
  accounts remain supported with an explicit provenance warning.
- New testimony classifications require the same independent-source gate as
  sensitive pinning. Duplicate/same-source evidence cannot upgrade archive;
  unknown origins need two distinct sources. Direct `remember(tier="testimony")`
  is rejected; callers must capture archive and supply actual corroboration.
- Every corroboration is audited, including duplicates and non-upgrading records.
- `unpin(memory_id, reason?)` audits removal as `unpin` or an already-unpinned/
  unknown ID as `unpin_noop`. Explicit blank reasons fail; omitted/null reasons
  retain legacy compatibility with an honest missing-reason audit marker.
  Python/MCP return shapes are unchanged; failed auditing rolls back removal.

### Added
- Optional `search(query, mode="semantic"|"hybrid")` in Store and MCP, backed by
  a pinned local multilingual E5 encoder. The semantic extra/model download is
  explicit; ordinary recall and default dependencies remain model-free.
- Encoding runs outside SQLite writer transactions, followed by fresh eligibility
  and priority checks. A bounded derived-vector cache cannot resurrect forgotten
  rows or upgrade source authority. No schema migration is added.
- On unchanged LoCoMo evidence budgets, hybrid mean Recall@5 improves from 42.76%
  to 51.90%, and multi-evidence recall from 17.02% to 26.04%; all 69 regressed
  questions remain in the public trace. This is a public reference set, not a
  blind test. Corpus, fixed model/fusion settings and actual MCP acceptance are
  recorded in docs/benchmarks/2026-09-18-semantic-results.md.
- 31 search/provider/benchmark/transport tests brought that stage to 298 tests.
- Public two-track benchmark: 120 frozen bilingual history episodes, 990 explicit
  protocol assertions, and pinned LoCoMo evidence retrieval with equal budgets
  for the product, reference BM25 and recency. Includes hash/coverage checks,
  complete JSON traces, negative controls and a credential-free CI artifact.
- External baseline report: 1,527 eligible questions, 42.76% mean evidence recall
  for both product and reference BM25; exclusions and limitations are published.
  This is not official QA accuracy or a comparative product leaderboard.
- 28 benchmark harness tests brought that stage to 267 tests, including denominator
  preservation when implementation or scoring fails partway through a case.
- Narrative dependency issues, persistent invalidation metadata and
  `review_narrative(id, note)` in storage/MCP. Default recall suppresses stale
  text and returns `narrative_review`; explicit inspection preserves history.
- Serialized additive migrations with failure rollback and concurrent startup
  coverage; new audit fault tests, two-order races and a seeded history sequence.
- Asserted JSON evaluation, 12 bilingual queries over 20 fixture documents,
  independent BM25 numerical tests and evaluator negative controls.
- The history-integrity stage brought the suite to 239 tests. Protocol/migration contracts and evaluation
  scope are documented separately from claims about real-world agent quality.
- Two cross-session MCP cases cover known/unknown origins through four fresh
  client/server pairs: sensitive pin denial, independent corroboration, persisted
  anchor recall, reasoned unpin, and legacy no-op auditing. Full suite: 179 tests.
- Reproducible guided Codex acceptance prompts and client setup notes, separate
  from deterministic CI tests; no hosted-model dependency is added to pytest.
- Read-only `provenance(memory_id)` in Store and MCP reports source labels and
  current corroboration sufficiency, warning on unsupported historical testimony.
  Old classifications are preserved without invented evidence or audit entries.
- 21 regression cases for provenance and unpin, including real stdio, concurrent
  removal and audit fault injection. That stage brought the suite to 177 tests.
- 28 transaction tests: 15 audit failure scenarios, nested/partial refresh
  failures, controlled connection and process races, denied-pin persistence,
  and busy BEGIN/COMMIT recovery. This stage brought the suite to 156 tests.
- CI runs on PRs targeting `codex/**` branches as well as `main`, allowing
  this stage to be reviewed and tested on top of the 1.1.1 patch.

### Fixed
- Forgetting and unsupported sensitivity invalidate linked narratives. Restoring,
  reviewing or corroborating a source cannot silently approve a stale synthesis.
- Legacy malformed narrative links fail closed without breaking recall; legacy
  unsupported canon remains inspectable but is not prioritized.
- BM25 uses logarithmic IDF and correct positive average length, counts each
  query term once, and returns zero scores for empty/stopword queries.
- Malformed caches fall back to content; CJK runs no longer join across embedded
  ASCII words, including affected old token caches.
- State-changing Store calls now reserve SQLite's writer before reading
  preconditions and commit business/audit changes together. Separate
  connections and processes cannot create duplicate current narratives,
  active canon memberships, or open conflicts, or race pinning against forgetting.
- Failed audit writes, partial review refreshes, and commit/lock errors roll
  back before a connection is reused. A later successful call cannot commit
  a previous failed call's partial state.
- Nested calls such as `recall()` -> `due_for_review()` share the outer
  transaction. An intentional sensitive-pin denial still commits its denial
  audit and returns the original `PermissionError` contract; failed denial
  auditing rolls back normally.

### Tradeoff
- State-changing calls serialize at SQLite's writer reservation, including
  `recall()` because it refreshes review status. The existing 30-second busy
  timeout remains in effect. Existing historical anomalies are not repaired.

## [1.1.1] — Unreleased

Reliability fixes targeting 1.1.1; no database schema changes.

### Fixed
- `flag_sensitive` now returns its storage result directly over MCP, including
  `unpinned_by_sensitivity`. Previously it committed the change, then raised
  an `AttributeError` while trying to convert an already-converted dictionary.
- Invalid `remember` and `narrate` inputs now return the same structured
  `error` / `message` / `hint` payload as other guarded tools.
- Empty and whitespace-only original sources now receive the same
  two-distinct-source corroboration requirement as `source=None`, including
  rows already stored in existing databases.
- `pin()` rejects forgotten memories, preventing new hidden anchors from
  being created while their memories are tombstoned.
- `recall()` deduplicates memories shared by multiple canon scopes before
  allocating its limit. `list_canon()` retains all scope memberships.
- `recall()` omits conflicts with forgotten participants. Full conflict
  history remains inspectable through `list_conflicts()` (now including each
  participant's forgetting timestamp), and restoration resurfaces open conflicts.
- `recall()` refreshes interpretation review status before assembling its
  sections, preventing contradictory current/stale values on the first read.

### Added
- 14 regression cases, including five tests that start the real server and
  call its tools over MCP stdio. The suite now contains 128 tests.
- A `test` installation extra and CI coverage for MCP 1.2.0, latest 1.x, and
  latest 2.x, alongside the existing OS/Python matrix.
- A prioritized [reliability plan](docs/plans/2026-09-17-reliability-roadmap.md).

## [1.1.0] — 2026-09-17

### Added
- Deterministic English/Chinese sensitivity screening at `remember()` time,
  with `auto_flag_sensitive` audit entries for recognized injection patterns.
- `due_for_consolidation(days, limit)`: an oldest-first queue of active
  working memories for the session-boundary consolidation ritual.
- MCP 1.x / 2.x server import compatibility.
- Three-condition poisoning check: naive persistence, an omitted sensitivity
  flag caught by automatic screening, and explicitly flagged enforcement.

## [1.0.0] — 2026-09-16

First release-candidate version: eight memory-studies modules complete,
recall upgraded from substring filtering to lexical ranking, error reporting
made self-correcting for LLM callers.

### Added
- **BM25 lexical recall ranking**: `recall(query)` now ranks ordinary
  memories by an in-process Okapi BM25 score over normalized tokens
  (CJK character-bigrams + latin words + stopword list; no embedding/model
  dependency). Token caches are written at `remember()` time and backfilled
  on the fly for rows created by older versions. Fuzzy queries like
  “上次那个方案” now surface “初步方案已定…” that a substring match missed.
  Each hit carries a `relevance` score.
- **Structured, self-correcting tool errors**: all guard-raising tools
  (`promote`, `pin`, `forget`, `canonize`, …) return
  `{"error", "message", "hint"}` on protocol violations instead of bare
  tracebacks — e.g. a premature `pin()` comes back with the hint to call
  `promote(memory_id, reason)` first, so an agent can correct itself without
  a guessing round-trip.
- Four-round simulated-daily-use E2E validation over one persistent database
  (identity capture → cross-session recall → fuzzy query + `narrate()` with
  memory_ids traceability → simulated prompt-injection refused storage
  entirely), plus a 9-point stress/boundary round (BM25@5000 in 127ms,
  8-thread concurrent writes, pre-v1.0 row backfill, limit-boundary dedup).

## [0.9.0] — 2026-09-16

Six cross-module interaction bugs found in a dedicated review round, all
fixed with regression tests.

### Fixed
- `recall()` duplicated canonized memories (a memory appeared in both the
  `canon` and `memories` sections; an anchor+canon memory appeared twice).
  Now strictly deduplicated — anchor+canon shows under `anchors` only.
- `recall(limit=N)` only bounded the `memories` section (up to 12+8+10=30
  entries returned for `limit=10`). `limit` is now a global budget across
  anchors + canon + memories; anchors remain the sole exception (always
  returned in full, by design).
- `forget()` lacked a canon guard: a canonized memory could be forgotten
  directly, leaving an orphaned active canon entry. Now mirrors the anchor
  guard — `decanonize()` / `end_scope()` first.
- `flag_sensitive()` did not lift an existing pin, so a memory pinned before
  being recognized as security-sensitive kept its always-surfaced anchor
  status. Retroactive flagging now auto-lifts unverified anchors (logged as
  `unpin_by_sensitivity`; reversible by corroborating and re-pinning).
- `mark_conflict()` accepted forgotten memories; now rejects with an
  explicit error (conflicts describe live framed versions, not tombstones).
- `independent_corroboration_count()` was too lenient for memories with
  `source=None`: any single corroboration counted as independent. Unknown
  origin now requires two distinct corroborating voices.

## [0.8.0] — 2026-09-15

### Added
- **Social framing / multi-perspective memory** (Halbwachs, *cadres
  sociaux*): memories can carry a `frame`; conflicting framed versions are
  declared with `mark_conflict(a, b, reason)` — **neither is deleted or
  overwritten** — and settled with `resolve_conflict(reason,
  adopted_memory_id?)`, which records the outcome without deleting the
  losing version. `recall()` surfaces open conflicts explicitly and
  supports a `frame` filter.
- Additive schema migrations for pre-existing databases (`_migrate()`,
  idempotent, covered by a hand-built legacy-db test).

## [0.7.0] — 2026-09-15

### Added
- **Canon / archive circulation** (Assmann, *Kanon/Archiv*): a task-scoped,
  rotating "canon" distinct from permanent anchors. `canonize(memory_id,
  scope, reason)` adds a consolidated memory to the active canon;
  `end_scope(scope, reason)` decommissions a whole scope at once when the
  task ends. Exiting the canon is not forgetting and not downgrading —
  entries stay consolidated, just no longer prioritized. Solves context
  bloat without the everything-is-an-anchor trap.

## [0.6.0] — 2026-09-15

### Added
- **Narrative integration** (Ricoeur, *identité narrative*): `narrate(content,
  reason, memory_ids?)` gives an agent-composed synthesis a first-class,
  versioned, accountable existence. Previous narratives are superseded, not
  deleted, so the story itself has a history. `recall()` returns the current
  narrative alongside the discrete memory list.

## [0.5.0] — 2026-09-15

### Added
- **Source criticism / memory-poisoning defense** (Ricoeur, *l'abus de
  mémoire*): `security_sensitive` flag (at `remember()` time or via
  `flag_sensitive`); pinning such a memory requires at least one
  `corroborate()` from a source distinct from the memory's own source, else
  `PermissionError` + `pin_denied` audit entry. A single injected claim can
  no longer promote itself into the anchor set.
- Deterministic mechanism tests (`usefulness_test.py`: anchors survive 200
  noise memories where a naive recency baseline loses the identity fact at
  10; `poisoning_test.py`: the sensitivity flag is the difference between
  an injected claim becoming a permanent anchor and being blocked).
- Sharpened `AGENT_GUIDE.md` trigger-word guidance (incl. Chinese cues).

## [0.4.0] — 2026-09-15

### Fixed
- **Thread-safety bug**: bare `sqlite3.connect()` raised `ProgrammingError`
  under concurrent tool calls. Fixed with `check_same_thread=False` +
  instance-level `RLock` serializing all public methods + busy_timeout.

### Added
- Reliability test battery (thread safety, multi-process concurrency,
  persistence across restart, 5k-memories scale, edge-case suite with
  SQL-injection-shaped inputs, nested db path auto-create) — 6/6.

## [0.3.0] — 2026-09-14

### Added
- **Accountable forgetting** (Ricoeur): `forget(reason)` tombstones a memory
  (content retained, excluded from recall; anchors must be `unpin()`-ed
  first); `restore(reason)` reverses it; `list_forgotten()` inspects
  tombstones.

## [0.2.0] — 2026-09-14

### Added
- **Provenance tiers** (Ricoeur): `archive` / `testimony` /
  `interpretation`; corroboration upgrades archive→testimony;
  interpretation-tier memories require periodic `review()`;
  `due_for_review()` surfaces overdue ones.

### Changed
- Consolidation/anchor hardening: empty reasons rejected everywhere;
  re-promoting updates the reason without resetting `consolidated_at`;
  pinning requires prior consolidation.

## [0.1.0] — 2026-09-14

Initial local prototype.

### Added
- **Consolidation protocol** (Assmann): working→consolidated via explicit
  `promote(reason)`, audit-logged.
- **Anchor memory** (Nora): `pin(reason)` / `unpin()`; anchors always
  surfaced first, never compete on relevance; soft limit 12.
- MCP Server (FastMCP) exposing the protocol; SQLite storage.
