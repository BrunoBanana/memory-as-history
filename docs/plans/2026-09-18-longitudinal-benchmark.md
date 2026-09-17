# Longitudinal Benchmark Implementation Plan

**Goal:** Publish and run a reproducible memory-history benchmark with at least
100 explicitly specified episodes, honest comparison baselines, inspectable
failures and an external-data retrieval track.

**Architecture:** Two separate tracks prevent conflating protocol compliance with
open-ended agent quality. A frozen synthetic corpus drives the real Store through
public calls and connection restarts. A text-only external retrieval adapter
compares the real recall path against recency and independently scored BM25 under
identical content budgets; scoring labels never enter indexing or retrieval.
No hosted model, secret, production database or new runtime dependency is needed.

**Stack:** Python stdlib, SQLite, existing pytest/MCP environments.

The user approved starting the public longitudinal benchmark after the 1.2 RC.
Continue through implementation, validation, report, CI and integration without
another design-approval pause. Use an isolated worktree and TDD for the harness.

## Alternatives and decisions

- Synthetic-only conformance is reproducible but favors the protocol's own rules.
- External end-to-end QA also measures reader/judge behavior and requires separate
  model budgets; it cannot substitute for state-transition verification.
- Choose separate conformance and external retrieval tracks. Do not publish one
  composite leaderboard or imply that simple baselines represent other products.

### Track A: history-v1

Freeze 120 synthetic episodes: 12 families × 5 situations × 2 languages. Pair
translations in the same split, with 72 development / 48 test episodes. These
are 12 correlated templates and author-visible splits, not 120 independent
real-user histories or a genuinely blind holdout. Publish the generator,
expanded cases, explicit expected outcomes and SHA-256 before scoring.

Families: anchors under noise; canon rotation; fact replacement; restoration
requiring narrative review; repeated known sources; unknown origin; sensitive
synthesis; retroactive sensitivity; overdue interpretations; framed conflicts;
narrative version preservation; lexical recall after restarts. Every episode
crosses at least three connection lifetimes. Time-sensitive review is triggered
via the public due_for_review(days=0), not silently altered database rows.

Score required presence as well as forbidden exposure, guard outcomes, source
counts, narrative review, conflict visibility, audit and historical preservation.
Missing output is a failure, never an automatic safety success. Every check has
an explicit denominator and trace. Report per-family/language/split counts;
there is no population accuracy interval for this designed conformance corpus.
The product must meet all declared contracts. Deliberately disabled retention,
withdrawal, source checks or auditing must be detected by negative controls.

### Track B: external evidence retrieval

Use a pinned upstream LoCoMo text dataset, outside Git and the wheel. Preserve
its separate CC-BY-NC-4.0 attribution; do not copy conversation text/answers into
reports. Only conversation turns (including available textual image captions)
enter indexing, never QA answers, evidence labels or question text. No images
are downloaded. Gold dialogue IDs are read only by the scorer.

Compare (1) Memory as History ordinary recall, (2) recency, (3) reference BM25
sharing the tokenizer but implementing the equation independently. All see the
same turns and chronological insertion order, with no privileged promote/pin
annotations. Apply the same whole-item content budget (top 5, at most 4096 UTF-8
content bytes); report selected bytes and cases excluded from scoring. This is
not a model token budget and not the official LoCoMo QA score.

Report evidence-turn Recall@5, any-hit rate, MRR@5, per-category and conversation
breakdowns, paired wins/ties/losses, query latency and database size. Exclude
adversarial/no-evidence/unresolvable annotations explicitly and account for every
question. Never report skipped questions as correct abstentions. Ten source
conversations are the unit of dependence; do not treat thousands of questions
as independent user trials or claim statistical/industry superiority.

## Implementation and verification steps

1. Commit this design; generate and freeze data/manifest before running systems.
2. Write failing harness tests: corpus validation, scoring arithmetic, missing
   output, label isolation, budget fairness, dataset hash and failure exits.
3. Implement package modules under src/memory_as_history/benchmarks/ and a
   generator under scripts/. Run protocol corpus and targeted tests.
4. Add reference retrieval, pinned external adapter and CLI JSON/report output.
   Assert toy known outcomes, negative controls, coverage accounting and parity.
5. Run both tracks; inspect failures without editing expected outcomes to pass.
   Record exact data/code hashes, environment, commands and limitations in
   docs/benchmarks/. Add credential-free protocol CI and artifact collection.
6. Validate baseline/full suite, three MCP versions, installed wheel and CLI
   error behavior. Open PR, inspect exact-head CI, merge and verify main.

External references: [LoCoMo official repository](https://github.com/snap-research/locomo)
and [LongMemEval official repository](https://github.com/xiaowu0162/LongMemEval).
LongMemEval's oracle variant contains evidence sessions only; it is not chosen
as a distractor-retrieval comparison. Official QA evaluation remains separate.
