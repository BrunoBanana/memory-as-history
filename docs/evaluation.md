# Evaluation scope and promotion criteria

Run `python usefulness_test.py --json` for versioned JSON metrics and exit code
0 on success, 1 on an unmet contract. Runtime/setup errors also exit nonzero.
Run `python -m pytest tests/test_evaluation.py -q` to verify the evaluator itself:
removing anchor retention or replacing lexical ranking with default order must
make it fail. CI runs these checks without model credentials.

The committed fixture has 20 documents and 12 queries, six English and six
Chinese. Each query has one manually assigned relevant document. Metrics are
Hit@1 (fraction with that document first) and mean reciprocal rank. Current
acceptance requires all 12 at rank 1. This catches known regressions on this
fixture; it is small, synthetic, and has no statistical generalization claim.
Numerical tests independently check the BM25 equation and input boundaries.

The anchor scenario checks retention at 3, 10, 50, and 200 later noise rows with
limit 5. The recency-only baseline keeps the identity only when it fits within
that window; an explicit anchor survives every tested case. This baseline is
not a proxy for Mem0, Letta, Zep, embeddings, or other products.

Deterministic protocol tests assert state, rollback, version chains, provenance,
source withdrawal and review across separate client/server processes. Guided
Codex acceptance checks an actual client in fresh conversations, with synthetic
data, tool-response assertions and an independent SQLite snapshot. A guided
success does not establish spontaneous tool selection or broad model reliability.

## Requirements before claiming a reference implementation

The 1.2 RC closes the specified reliability roadmap. The first public benchmark
now separates [history conformance from external evidence retrieval](benchmarks/README.md).
The [2026-09-18 report](benchmarks/2026-09-18-results.md) publishes all 120 synthetic
episodes and 1,527 scorable LoCoMo questions with frozen data hashes, exclusions
and traces. Protocol checks pass; ordinary retrieval ties the reference BM25
ranker at 42.76% mean evidence recall. This does not establish industry leadership.

The [optional semantic follow-up](benchmarks/2026-09-18-semantic-results.md) uses
the same data and budgets: hybrid recall reaches 51.90% overall and 26.04% on
multi-evidence questions (previously 17.02%). Model/fusion settings were frozen
before that run; all regressions are published. A separate bilingual development
set was frozen before encoding, but is small and author-visible. These results
do not turn the already inspected LoCoMo reference set into a blind test.

Remaining evidence requirements are:

1. Independently reproduce the public workload and collect genuinely unseen
   histories. The 120 published episodes cover both languages and the intended
   state transitions, but derive from 12 correlated templates. Their public
   dev/test split is author-visible, not an independent holdout.
2. Extend the recorded recency/lexical/semantic comparisons with temporal
   baselines under matching budgets. Validate multi-evidence retrieval, online
   updates, stale-fact exposure and audit completeness outside authored protocol
   cases. Measure storage growth and use conversation-aware uncertainty estimates;
   do not treat questions from the same history as independent trials.
3. Compare guided versus unguided clients across repeated model runs. Report
   memory use and source correctness separately from the storage mechanism.
4. Validate larger histories, backup/restore and interruption recovery, and
   address source authentication/semantic entailment before expanding security
   claims. Current source labels and known-pattern screening do not solve these.

These remaining criteria are not results already achieved by this RC. The
external evidence track does not measure official LoCoMo QA or abstention.


## Explicit history structure

The [2026-09-19 report](benchmarks/2026-09-19-history-results.md) measures time
filters, caller-asserted links and one-hop session neighbors separately. Its
challenge corpus and test split are authored and correlated. External adjacency
does not improve complete multi-evidence coverage and has 107 regressions against
hybrid. Real two-process MCP checks establish protocol behavior, not autonomous
source interpretation, causal reasoning, generated-answer quality or abstention.

## Claims and knowledge-history preview

The [1.3 contract](knowledge-history.md) adds separate judgments/evidence,
recording-time queries, scoped narratives and independent archive budgets.
`tests/test_knowledge.py`, `tests/test_historical_views.py` and
`tests/test_knowledge_protocol.py` contain 70 new authored cases, including
parameterized invalid-input/fault cases. Do not count them as 70 independent
real-world histories. The example in `examples/historical_claims.py` is also authored.

These checks establish explicit-call behavior: no late-record leakage, stable
claim kinds, common-origin reporting, atomic revision, current forgetting overlays,
dependency invalidation and separate narrative scopes. Real stdio tests cover
process restarts and protocol discovery. They do not test whether an unguided
model notices evidence, assigns its stance correctly, distinguishes sources,
avoids unsupported causality/consensus or chooses the right tool spontaneously.

The nine scenarios in the reading report remain a framework for model evaluation.
Storage fixtures now exercise relevant mechanics; semantic support and useful
abstention still require independent adjudication. Collect previously unseen
histories from independent authors, fix answer/evidence criteria before running,
and report repeated unguided-client results, errors and invocation cost. Preserve
separate scores for extraction, source support, temporal correctness, perspective
coverage and unsupported claims. No new external retrieval/QA score is claimed
by this preview; the previously published negative results remain applicable.
