# Reproducible history benchmarks

This benchmark separates **history protocol conformance** from **external
evidence retrieval**. Neither score is an end-to-end agent accuracy score, and
there is no combined leaderboard. See the [measured results](2026-09-18-results.md)
and the [design frozen before scoring](../plans/2026-09-18-longitudinal-benchmark.md).
No hosted model, API key or production memory database is used.

## Run from a checkout or an installed wheel

```sh
python -m pip install -e '.[test]'
python -m pytest tests/test_benchmarks.py -q
python -m memory_as_history.benchmarks protocol --output .benchmark-results/protocol.json
python -m memory_as_history.benchmarks protocol --split test --output .benchmark-results/protocol-test.json
```

The same module command works after installing the project's wheel; its synthetic
data and manifests are packaged. Every run uses temporary SQLite databases and
cleans them up afterward. CI runs all protocol cases and uploads JSON artifacts.
Exit codes are 0 for satisfied protocol contracts, 1 for failed contracts, and
2 for handled input/setup errors. Unexpected runtime failures also exit nonzero.
The external command exits 0 when measurement completes, regardless of score;
it has no preset quality threshold. Zero scorable questions is an error.

The external data is downloaded separately. The command validates its exact hash
before indexing. These commands use the pinned upstream version:

```sh
mkdir -p .benchmark-data
curl --fail --location \
  https://raw.githubusercontent.com/snap-research/locomo/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/data/locomo10.json \
  --output .benchmark-data/locomo10.json
python -m memory_as_history.benchmarks locomo \
  --data .benchmark-data/locomo10.json --max-items 5 --max-bytes 4096 \
  --output .benchmark-results/locomo.json
```

LoCoMo is from [snap-research/locomo](https://github.com/snap-research/locomo),
released under [CC-BY-NC-4.0](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/LICENSE.txt).
Its conversation data is not redistributed with this project's MIT package.
The pinned [manifest](../../src/memory_as_history/benchmarks/data/locomo.manifest.json)
records source, attribution, license and SHA-256. Reports retain IDs, scores and
timings, not upstream conversation text, questions or answers. No media is fetched.

## Track A: history-v1 corpus card

The [corpus](../../src/memory_as_history/benchmarks/data/history-v1.json) contains
120 episodes: 12 authored templates × 5 situations × 2 languages (English and
Chinese). The 72 development and 48 test episodes separate situations; translation
pairs stay together. These splits are public and author-visible. They are not a
blind holdout, and template variants are not independent user histories.

The [generator](../../scripts/build_history_corpus.py), expected outcomes and
[manifest](../../src/memory_as_history/benchmarks/data/history-v1.manifest.json)
were committed in `8a42c43` before the runner and measurements. Re-running the
generator must preserve the manifest hash for history-v1; changed expectations
require a new corpus version. The synthetic corpus is MIT licensed.

Every episode invokes explicit public Store methods and spans at least three
SQLite connection lifetimes. This verifies persistence across close/reopen, not
autonomous agent decisions or process-crash recovery. The separate MCP session
tests cover actual client/server process restarts. Review expiry is triggered
through `due_for_review(days=0)`, without secretly editing timestamps.

| Family | Contract checked |
| --- | --- |
| anchor_noise | Retain an explicitly established anchor through noise and restarts |
| canon_rotation | Retire a finished scope without erasing its history |
| fact_update | Withdraw an outdated fact and retain the updated account |
| restore_review | Require explicit narrative review after source restoration |
| known_source / unknown_source | Enforce source-count gates; duplicates do not add support |
| sensitive_synthesis | Require support for every sensitive narrative dependency |
| retroactive_flag | Revoke unsupported priority after sensitivity changes |
| interpretation_review | Surface overdue interpretations for review |
| framed_conflict | Preserve conflicting perspectives and frame boundaries |
| version_history | Preserve superseded narrative versions |
| lexical_restart | Retrieve known text after connection restarts |

Each assertion records its read path, expected operator/value, actual value and
pass/fail status. UUIDs normalize to fixture aliases. Exact equality, membership
and exclusion checks all fail on missing output, including missing lists/nulls.
`stale_exposure` and `unsupported_priority` are **contract pass rates**, not
observed exposure rates in a user population. `audit_coverage` checks the listed
events; it is not a proof of complete auditing for all possible histories.

An unexpected operation or scorer exception fails the episode. Remaining
predeclared assertions stay in the denominator as failed, separately marked
`blocked`; already scored assertions keep their outcomes. Reports include all
assertions, grouping by family/language/split, connection counts, elapsed time
and final database bytes. Disabling retention, forgetting, source guards or
auditing must fail the evaluator's negative controls.

## Track B: external evidence retrieval

The adapter treats each conversation independently. All its turns are ingested
in session/turn order before its questions are evaluated; this is full-history
retrieval, not online forecasting. Each document contains speaker and text plus
the upstream textual image caption if present. Session dates, event summaries,
observations and answers are not indexed. All three systems receive the same
documents, without gold-derived pinning, promotion, canon or narrative hints.

| System | Ranking policy |
| --- | --- |
| memory_as_history | Real persisted `Store.recall(query=..., limit=all_turns)` after reopen |
| reference_bm25 | Independent BM25 equation, k1=1.5 and b=0.75, same product tokenizer |
| recency | Latest turns first |

BM25 ties use latest insertion order, as does the product. The reference is an
in-memory lexical ranker, not an independent tokenizer or competing memory
product. No semantic baseline or hosted reader/judge is included.

Each ranking passes through the same selector: at most five whole turns and
4096 **UTF-8 content bytes**. Oversized turns are skipped in rank order, without
truncation; relevant oversized turns remain misses. Metadata bytes and model
tokens are not counted. Changing either budget defines a different run. Retrieve
all candidates before budget selection so a long early item cannot hide later
fitting items. The recorded selected IDs and byte counts allow direct auditing.

Evidence labels are stripped of surrounding whitespace and deduplicated, then
matched exactly to dialogue IDs. Labels enter scoring only, not indexing or
ranking. Exclusions apply in this order: category 5 (`adversarial`), empty evidence
(`no_evidence`), any unresolved evidence ID (`unresolved_evidence`). Malformed
annotations are not repaired after seeing scores. Every original question gets
either a result or an exclusion record. Excluded questions earn no credit for
abstention; they reduce the reported coverage.

For each eligible question, let G be unique gold dialogue IDs and S be the
budget-selected IDs:

- Recall@5 = |S ∩ G| / |G|; report the mean across eligible questions.
- Hit@5 = 1 if any selected ID is gold, otherwise 0.
- MRR@5 = reciprocal rank of the first selected gold ID, or 0 on a miss.

These measure evidence-turn retrieval, **not official LoCoMo answer accuracy**.
Per-category/conversation breakdowns and paired recall wins/ties/losses accompany
the totals. Gold-only diagnostics are computed after ranking: counts exceeding
the item/byte budgets, the mean achievable recall under those budgets, and
evidence turns without query-token overlap. The budget ceiling assumes perfect
knowledge of gold and is not a measured system result.

Queries within ten source conversations are correlated. We publish their separate
scores without an iid confidence interval over questions or a superiority claim.
Latencies include selection and, for the product, SQLite recall; reference
rankers use memory. Indexing times and SQLite file sizes are descriptive snapshots,
not a controlled performance contest or storage-growth curve.

## Evidence and remaining work

Reports record exact dataset, selected-case, storage-code and runner-file hashes,
Python/platform/SQLite/package versions. The published compressed JSON traces
can be opened with `gzip.open(path, 'rt')` and parsed using `json.load`. The
summary records the runner commit and SHA-256 of both raw and compressed reports.
Timings, environment strings and generated timestamps need not repeat; check
selected IDs, assertion outcomes, counts and scores for deterministic equality.

Independent reproduction, a genuinely unseen evaluation set, semantic/temporal
baselines, online history updates and repeated unguided client trials remain
necessary before claiming a reference implementation. This benchmark makes
those gaps measurable; its synthetic pass rate does not resolve them.
