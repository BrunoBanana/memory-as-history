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

Remaining evidence requirements are:

1. Independently reproduce the public workload and collect genuinely unseen
   histories. The 120 published episodes cover both languages and the intended
   state transitions, but derive from 12 correlated templates. Their public
   dev/test split is author-visible, not an independent holdout.
2. Extend the recorded recency/lexical comparisons with semantic and temporal
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
