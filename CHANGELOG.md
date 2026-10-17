# Changelog

All notable changes to this project are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [2.9.0] — 2026-10-17

Replayed 360-message sessions from real official English stores (via the
read-only replayer) and found the v2.7 length gate silently broken for
English, plus two smaller consistency fixes. Replay audit before / after:

| measure | store A | store B |
| --- | --- | --- |
| false version edges >160 chars | 29 / 0 | 19 / 1* |
| contradiction pairs (long paragraphs) | 120 / 0 | 0 / 0 |

*The one remaining >160-char edge is a Jaccard/coverage strong-overlap
path (coverage 0.77): genuinely overlapping long texts are still
versions — the length gate only gates predicate-domain versioning.

### Fixed

- **Length gate is now a character count.** `_DOMAIN_VERSION_MAX_TOKENS=48`
  counted stopword-filtered unique tokens. That was correct for Chinese
  (CJK bigrams survive filtering) but defeated for English: a 300-character
  Wikipedia paragraph collapses to <=48 content words after stopword
  removal, sailed through the gate, and generated assistant-vs-assistant
  false version edges and `negation_flip` false contradictions on real
  official stores (120/120 false contradiction pairs and 29/45 false edges
  were >160 characters). The gate is now `_DOMAIN_VERSION_MAX_CHARS=160`,
  language-neutral and immune to stopword filtering. Both version and
  contradiction detection use it.
- **Structured triples never enter contradiction detection.** Triples are
  injected facts with stable subject_id/relation_id keys; polarity signals
  are meaningless for JSON payloads and produced false pairs on real
  stores. Contradiction detection now skips either side that is a triple.
- **English pronoun subjects are shared-subject evidence.** Stopword
  filtering drops every pronoun from token sets, so "I live in Paris" →
  "I moved to Berlin" shared zero tokens while the Chinese equivalent
  ("李明住在福州" → "李明搬到上海") keeps its name and works. A matching
  sentence-initial personal pronoun (I/I, She/She…) now counts as the
  shared subject; a pronoun mismatch (I vs She) means a different subject
  and stays unshared. Genuine short English revisions supersede again.
- **Read-time same-speaker gate.** The write-time gate (v2.7) only lets the
  person who states a fact revise it; the read-time version discount is now
  consistent: a cross-speaker restatement in the search window no longer
  silently depresses the speaker's own fact. The internal speaker label is
  carried on search entries and stripped before the response leaves Search,
  so the protocol contract stays exactly {"id","content","score"?,
  "created_at"?} per entry.

### Verified

- Real Chinese store replay (150 messages): still 0 version edges /
  0 contradictions (the character gate did not loosen the Chinese result).
- Real English store replay: long-paragraph false contradictions 120 → 0;
  assistant-vs-assistant long false version edges 29 → 0 (store A).
- Short English statements still contradict ("She loves cats" vs
  "She hates cats") and still supersede ("I live in Paris" →
  "I moved to Berlin"); long English paragraphs (Wikipedia-style, >160
  chars) neither contradict nor version each other.
- Service interface tests: **198 cases**; full suite **646 passing**.

## [2.8.0] — 2026-10-17

Query-trigger fixes found by running review/causal/order question shapes
against a real 80-session store through the full Search path. English
questions are sentence-initial ("Summarize…", "What happened…",
"Timeline…"), and the summary / causal / order trigger patterns were
case-sensitive — capitalized English triggers silently never fired, which
crippled global synthesis (F1), causal-chain (B2) and chronological-order
(C2) answers on real corpora.

### Fixed

- **Case-insensitive English query triggers.** `_SUMMARY_QUERY_PATTERN`,
  `_CAUSAL_QUERY_PATTERN`, `_ORDER_QUERY_PATTERN` and `_CONTENT_YEAR_PATTERN`
  now match with `re.I`; rule and preference patterns already lower-cased
  the query before matching. "Summarize my recent experience" now triggers
  summary entries (was silently returning none).
- **Summary entry budget was broken.** `_summary_entries` stopped after the
  first entry for any realistic Top K (`len(entries) * 2000 >= top_k`
  compared characters against an entry count), so a review question saw one
  session's summary only. It is now a real entry cap — a fraction of Top K,
  at most 4 sessions — and summaries are ordered newest session first, so
  the answer leads with the recent state.
- **Summary points skip greeting lines.** `_point` took the first line of a
  message; messages that opened with "你好/谢谢/嗯嗯" contributed the
  greeting to the rolling summary. It now takes the first substantive line
  (pure greeting/filler patterns are skipped).

### Verified

- Real 80-session store: "Summarize my recent experience" returns 4
  summary entries (was 0 before the fix); Chinese review queries return 4
  (was 1); ordinary factual queries ("膝盖现在怎么样了", "我最近情况怎么样")
  still return zero summary entries — trigger discipline intact.
- Service interface tests: **190 cases**; full suite green.

## [2.7.1] — 2026-10-17

Cross-role UPDATE compatibility, found by a full-corpus scan of 1614 real
official stores. v2.7's same-speaker gate assumed the revising statement
and the stored fact share one source role; the scan shows stored triples
are almost all `user` (1617 vs 1 assistant) while **85 update
instructions arrive as `assistant`** — the gate would have silently broken
version detection for those revisions (D1 recall loss).

### Fixed

- **Structured triples are exempt from the same-speaker gate.** Their
  stable `subject_id`/`relation_id` key is exact same-fact evidence (far
  stronger than any token heuristic); an UPDATE delivered under a
  different role still supersedes the stored triple. The gate now applies
  per candidate instead of in SQL.
- **Legacy stores without a `source` column no longer crash Add.**
  Governance probes the column and degrades gracefully (gate disabled,
  change-signal and length gates still apply) — old stores stay readable
  and writable, per project principle.

### Verified

- Full-corpus scan (1614 stores, 515k rows): triples ~all `user`;
  UPDATE instructions `user` 565 / `assistant` 85 — the exact shape that
  motivated the exemption.
- Same-speaker gate still blocks plain cross-role restatements (user fact
  vs assistant restatement → no edge); real Chinese store reopened through
  MemoryService and written without crash; replay of 150 real Chinese
  messages still yields **0 version edges, 0 contradiction pairs**.
- Service interface tests: **184 cases**; full suite green.

## [2.7.0] — 2026-10-17

Precision gates on governance, discovered by replaying real conversation
stores through the full Add/Search pipeline. Before v2.7, same-domain +
shared-token was enough to record a version edge and a contradiction pair;
real long conversations restate the same domains constantly, so replaying
**150 real Chinese messages produced 1603 supersede edges and 180
contradiction pairs — nearly all false**. v2.7 makes revisions and
conflicts conservative on real shapes.

### Fixed

- **Change-signal gate**: a version edge now requires an explicit
  change-denoting predicate on either side (搬到/辞职/分手/改名/买了/moved/
  quit/divorced…). Generic markers ("已经", "开始", "后来", "现在") were
  ubiquitous in narrative text and turned the previous gate into a no-op
  (edges only dropped 1603 → 1462 with them); they are deliberately not
  signals. A flat "住在福州" beside "住在上海" with no change predicate is
  not a revision.
- **Same-speaker gate**: only statements by the same speaker form versions
  of each other. Interleaved user facts and assistant restatements/
  reassurance no longer supersede each other (a fact is revised by the
  person who states it).
- **Statement-length gate**: predicate-group domain versioning and
  contradiction detection only apply to short, statement-like texts
  (≤ 48 tokens). Long paragraphs — reassurance, analysis, multi-point
  plans — are not single facts and were the source of the false edges
  (assistant paragraphs mentioning 工作/职业 hit the occupation domain).
  Long texts still get revision treatment through the Jaccard/coverage
  path when they genuinely overlap; structured triples (v2.4) stay exempt
  because the stable subject_id/relation_id key is same-fact evidence
  regardless of length.

### Verified

- Replaying the same 150 real Chinese conversation messages after v2.7:
  **0 version edges, 0 contradiction pairs** (the corpus is state/emotion
  description with no genuine fact revisions — zero is correct), summaries
  6, rule-marked 72, retrieval probes still hit the right turns.
- Short-statement D1 keeps working across sessions ("李明住在福州" →
  "李明搬到上海了" records one edge); English restatements ("She lived in
  London" → "She moved to Berlin") unchanged; structured-triple UPDATE
  versioning unchanged.
- Service interface tests: **181 cases**; full suite green.

## [2.6.0] — 2026-10-14

English governance forms, driven by real-world memory corpora that are
predominantly English. Before v2.6, version/contradiction/revocation logic
was Chinese-only (住在/工作/喜欢/了/再…), so non-structured English
statements had **no** same-fact revision detection at all — only the
structured-triple path (v2.4) handled them.

### Added

- **English predicate groups** (word-boundary regexes): residence
  (live/lived/moved/relocated/settled…), occupation (work/job/joined/
  quit/resigned…), preference (like/love/hate/prefer/support/oppose/
  against…), acquired (bought/adopted/owns…), relationship (married/
  divorced/dating…), identity (born/birthday/graduated/age…), contact
  (phone/email/contact…). "She lived in London" → "She moved to Berlin"
  now records a `superseded_by` edge and ranks the new value first on
  current-state questions — previously 0 edges.
- **English negation** via word-splitting (`[a-z']+`, `endswith("n't")`)
  instead of `\b` regexes — Python's `\b` does not treat the apostrophe in
  "doesn't" as a boundary, so `\bn't\b` silently missed every contraction.
- **English opposite pairs** (like/hate, support/oppose, support/against,
  approve/reject, married/divorced, for/against) for D2 contradiction
  detection; **English revocation** ("no longer", "not anymore", "stopped",
  "quit", change markers now/anymore/these days) routes state changes to
  the version path instead of D2.
- **English subject-word sharing** for same-domain versions: the shared
  tokenizer drops English stopwords, so "She lived in London" / "She moved
  to Berlin" shared zero tokens; a dedicated English content-word set
  (pronouns kept, minimal stopwords) bridges that gap while the length gate
  still keeps same-length different facts apart.
- **KG relation-word synonym expansion**: capital↔capital city, located↔
  situated/location, born↔birthplace, country↔nation, population↔
  inhabitants, president↔leader, founded↔established, member↔part;
  residence family cross-links (live→moved/relocated, moved→live) so
  "where does she live now" recalls "she moved to Berlin" and the version
  discount floats the new state first.

Service interface tests: **177 cases**; full suite **625 passing**.

## [2.5.0] — 2026-10-14

Project identity cleanup: the Add/Search service is now fully neutral —
it is a standard capability of memory-as-history, not a wrapper around
any external competition.

### Changed (renames; breaking for downstream imports)

- `memory_as_history.aml` package → **`memory_as_history.service`**
  (same modules: `memory_service`, `governance`, `contract`, `chunking`,
  `temporal`, `server`).
- Tests `test_aml_*.py` → **`test_service_*.py`** (19 files).
- Entry point `memory-as-history-aml` → **`memory-as-history-server`**
  (`python -m memory_as_history.service`).
- Bookkeeping tables: new stores use neutral names (`memory_add_log`,
  `memory_sessions`, `memory_updates`, `memory_summaries`,
  `memory_contradictions`, `memory_time_ctx`). **Legacy `aml_*`-named
  stores keep working unchanged** — `governance.table_names` resolves the
  active names per store, so pre-existing databases are fully readable and
  writable with no migration.
- Runtime logs `[mem-add]`/`[mem-search]`/`[mem-auth]`; audit events use
  neutral names (`turn_expand`, `supersede`, `contradiction`, …).
- Env vars: `MEMORY_*` names are canonical; legacy `AML_*` names are still
  accepted. Dockerfile / compose / README updated accordingly.

Docs, changelog history and README were rewritten to describe the service
as a standard memory interface. Diagnostic scripts that read the legacy
stores (`scripts/diag_*.py`, `scripts/analyze_server_data.py`) stay local
and are not part of the published repository.

## [2.4.0] — 2026-10-14

Structured-triple version governance for knowledge-graph-shaped memories.
When a fact arrives as a structured triple — e.g. an explicit instruction
to replace the prior value of a subject+relation, carrying stable
subject/relation identifiers (QID/PID style):

    UPDATE: replace the prior value of this subject and relation
    with the following fact.
    {"subject": "Hyderabad", "relation": "continent", "object": "Eurasia",
     "subject_id": "Q1361", "relation_id": "P30", "object_id": "Q540"}

the stable ids ARE the same-fact key the governor needs. Earlier versions
missed this entirely: a replay of large English KG corpora showed 0
version edges and the OLD value ranking first — current-state questions
were structurally wrong on this shape.

### Added

- **`_triple_version`**: two structured triples with the same
  subject_id+relation_id (falling back to subject+relation without ids) are
  versions of one fact, independent of any token-similarity gate. The
  UPDATE instruction shape now records a `superseded_by` edge at write time
  and, at read time, the current object ranks first while the old value is
  discounted (current-state questions) yet still retrievable (history
  questions).
- **Different-triple guard**: two triples about different subject+relation
  are never versions — their shared template inflates coverage and must not
  collapse independent facts (Hyderabad ≠ Beijing just because both carry
  `subject_id`/`relation_id` fields).

### Verified on large English knowledge-graph corpora

- Replay of three representative stores through v2.4: **20 version edges**
  created (v2.3: 0), three UPDATE queries all returned the NEW object first
  (`n=5-6` evidence each). English KG-triple retrieval and wiki-passage
  retrieval also confirmed at 100% hit on the same stores (23/23 queries).
- Diagnostic/verification scripts live under `scripts/` (`diag_replay.py`,
  `diag_real_data.py`, `analyze_server_data.py`) — read-only, redacted
  output, corpus data never leaves the host.

### Changed

- `_version_decision` keeps its conservative length gate; a v2.4 attempt to
  add a bare coverage branch was rolled back after it misclassified
  same-length near-duplicates (第8条/第9条) and different-entity triples as
  versions — triple-key matching is the exact, correct mechanism for the
  structured shape.

Service interface tests: **170 cases** (contract / hybrid / governance /
temporal / summary / rules / contradiction / v1.6–v2.4), all green; full
suite **619 passing**.

## [2.3.0] — 2026-10-12

Memory-as-history 2.x is the deterministic capability line of the
retrieval/governance engine: every version below is an auditable
improvement to evidence recall and memory governance, landed on `main`
one commit at a time with the full suite green.

### Added (capability line)

- **v2.3 — cross-session preference aggregation.** Generic preference
  questions ("她喜欢什么 / what does she like") append the user's
  preference-domain memories across sessions so the answer generator sees
  the full preference set; specific questions still rank their BM25 hit
  first. Filtered by the governance domain so 爱/喜欢 hits like "爱人"
  never leak in.
- **v2.2 — relative-time anchoring.** "最近/上周/yesterday" questions
  answer with the newest slice of the conversation, even when the newest
  memory shares no word with the question ("我搬到上海了" vs "她最近住在
  哪里"). Boosted scores re-rank the evidence; order questions keep the
  chronological arrangement.
- **v2.1 — rule-QA boost + causal trigger expansion.** Rule questions
  ("她可以吃辣吗 / can she eat spicy food") lift rule statements above
  merely-similar memories; causal triggers grow (造成/影响/后果/来龙去脉/
  why did/what triggered/effect of) to keep widening the neighbor window.
- **v2.0 — preference revocation + age domain.** "我不喜欢咖啡了" is a
  state change, not a contradiction: negation + change marker (了/再) +
  known fact domain routes it to the version path (old value discounted)
  and suppresses the D2 pair, while stable conflicts ("她喜欢猫"/"她讨厌猫")
  still fire. Age domain (岁/多大了/几岁/属相) joins identity so
  "我今年25岁"→"我今年26岁" supersedes the older value.
- **v1.9 — chronological rerank.** Sequence questions (先后/顺序/按时间/
  timeline) reorder evidence by message time ascending so the story is
  narrated as it happened; ordinary questions keep relevance ranking.
- **v1.8 — synonym query expansion.** A small Chinese/English domain
  dictionary appends synonyms to the retrieval input only ("手机号"→"电话",
  "家"→"住在", mobile→phone, name→called); triggers still read the
  original query, and no dictionary hit returns the query unchanged.
- **v1.7 — richer summary points + causal neighbors.** Summary points keep
  160 chars (was 96) so a review answer has substance; causal questions
  widen the neighbor radius to 5 AND relax the token gate, so the middle
  step of a chain ("价格谈不拢") enters the evidence even without a shared
  token; contact domain (电话/号码/手机/邮箱/微信) restatements supersede
  older versions.
- **v1.6 — content-year time context.** Years named inside memory content
  ("2021年我住在福州") are indexed at write time and boosted by
  year-anchored questions, so the state at that time answers instead of
  the newest truth.
- **v1.5 — temporal expansion, domain versions, rules, contradictions.**
  Conservative relative-time clues (去年/上周/…) anchored on real message
  timestamps; predicate-group version detection (residence/occupation/
  preference/acquired/relationship/identity/contact) so same-domain
  restatements supersede older values; write-time rule memory (不能/禁止/
  always/never/no smoking…) and read-time rule entries; contradiction pairs
  recorded and surfaced at read time so the answer generator sees the
  conflict instead of one side.
- **v1.4 — hybrid retrieval hardening + trigger expansion.** Regression
  work folded into v1.5+.

### Add/Search service foundations (previously in [Unreleased])

- **Standardized Add/Search interface.** A zero-dependency HTTP service
  exposing the project's memory storage and
  BM25 retrieval through a fixed contract: synchronous
  `POST /add` (single-transaction request_id idempotency, per-user SQLite
  isolation, per-session ordered positions), `POST /search`
  (`{"data": [...]}` evidence array bounded by `top_k`, no answer leakage),
  and unauthenticated `GET /health`. Runs as `python -m
  memory_as_history.service` or the `memory-as-history-server` entry
  point. 26 contract tests (service + HTTP) and an end-to-end smoke
  simulation are included; full suite 454 passing at that stage.

## [1.3.1] — 2026-09-24

### Added

- **`--help` and `--version` on the packaged entry point.** Running the server
  by hand used to sit silently waiting for JSON-RPC on stdin — correct
  behavior that looks exactly like a hang, and running it by hand is the first
  thing a new user does. `memory-as-history --help` now exits with a usage
  note stating this is an MCP server rather than an interactive CLI, the
  client config snippet to paste, the two environment variables, and the
  profile/tool-count/database actually active in that process, so a
  misconfigured client is diagnosable without reading the source. Unknown
  arguments now fail with exit code 2 instead of starting a server on a typo
  and hanging the client. Four regression tests cover this.
- Project URLs (homepage, repository, changelog) in the package metadata.

## [1.3.0] — 2026-09-23

First tagged release. Promotes the 1.3.0a1 knowledge-history preview to a
stable version and adds tool profiles.

### Added

- **Tool profiles.** `MEMORY_AS_HISTORY_TOOLS` selects the MCP surface:
  `core` (default) exposes the 15 tools that carry the protocol loop —
  capture, consolidate, anchor, corroborate, forget, narrate, recall, audit
  — at roughly 3.5k tokens of tool manifest; `full` keeps all 46 tools.
  Profiles share one storage layer and one database: switching is an env
  var and a restart, never a migration. Five new stdio protocol tests pin
  the default surface, the corroboration gate on `core`, the full surface,
  the unknown-value fallback and cross-profile database interoperability.

### Previously integrated 1.3.0a1 work

- Separate immutable claims from captured material, with claim-specific support,
  challenge/context evidence, exact quote checks and declared common-origin groups.
  Material type, capture context and provenance labels remain separate dimensions.
- Record explicit adoption, revision and withdrawal in a claim event ledger.
  Recording-time queries exclude late evidence and later decisions. Revisions
  share one atomic recording time; concurrent alternatives cannot both win.
- Overlay current source access on historical claim queries. Forgotten material
  redacts derived text; restore does not silently re-adopt judgments. Sensitive
  claims retain existing source-label guards; neither origins nor adoption prove truth.
- Maintain independent narrative version chains by scope, perspective/coverage
  metadata, and explicit claim/relationship dependencies. Relationship retirement
  and evidence changes invalidate affected accounts. Discovery withholds stale text;
  legacy deliberate narrative inspection retains historical text.
- Add model-free `search_archive` with strict independent result budgets, filters
  across all material and explicit coverage counts. Anchor/canon priority cannot
  consume its result slots. It is not semantic proof or a historical state replay.
- Additive atomic migrations preserve legacy tiers, text and audits, with no
  fabricated historical claims. All new operations are exposed over MCP. A
  disposable bilingual-context example and detailed migration/API guide are included.
- Correct simplified theory attribution and expose the limit of legacy provenance
  counts: source-label distinctness is not verified independence. Existing fields
  and gate semantics remain, with two additive explanatory provenance fields.

### Repository note (2026-09-22)

The GitHub repository was recreated from a clean history on 2026-09-22 to
remove leaked committer identity data that GitHub's permanent pull-request
refs retained after history rewriting. Pull requests #1–#10 no longer exist
in this repository; their content is merged into `main` and their planning
records live in `docs/plans/`. Stars, watchers and fork graph were reset
(all were zero). Package versions, source code and test suites are
unchanged.

### Previously integrated 1.2 RC work

1.2.0 release candidate (`1.2.0rc1`), not yet published. Additive narrative/context schema and relationship migration; existing text and history preserved.

### Changed
- MCP string arguments preserve literal text across supported SDK versions,
  including numeric/exponent-shaped legacy IDs and optional fields containing
  `"null"` or JSON-shaped text. Structured list arguments retain SDK parsing.
  Nine real stdio regressions brought that stage to 307 tests.
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
- Explicit event/session metadata, audited context corrections and chronological
  inspection. Unknown event times stay null; corrections invalidate dependent
  narratives. Additive nullable columns, relationship table and unique indexes.
- Retractable, directed caller-asserted relationships and optional one-hop
  `search_history`, with fresh eligibility checks and inspectable selection paths.
  Relationships never confer corroboration or historical authority.
- Frozen 48-case bilingual history challenge, session-only external ablations,
  full miss diagnosis and two-process MCP acceptance. 42 regressions bring the
  suite to 349 tests; evaluation limitations and negative results are preserved.
- Optional `search(query, mode="semantic"|"hybrid")` in Store and MCP, backed by
  a pinned local multilingual E5 encoder. The semantic extra/model download is
  explicit; ordinary recall and default dependencies remain model-free.
- Encoding runs outside SQLite writer transactions, followed by fresh eligibility
  and priority checks. A bounded derived-vector cache cannot resurrect forgotten
  rows or upgrade source authority. The semantic-search stage itself added no schema migration.
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
