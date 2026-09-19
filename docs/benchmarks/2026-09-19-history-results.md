# Chronology and related evidence — 2026-09-19

This iteration adds explicit chronology and accountable evidence links. **Simple
session-neighbor expansion did not improve complete multi-evidence coverage on
LoCoMo**: it remains 35/405 (8.64%), while multi-evidence recall falls from 26.04%
to 23.99%. Existing hybrid `search` retains its ranking; history expansion is an
explicitly selected heuristic, not a proven replacement.

## Fixed experiment

The [diagnosis](2026-09-19-history-diagnosis.md) covers all 1,527 eligible questions
and all 69 prior hybrid regressions. 326/405 multi-evidence questions span sessions;
239/1,372 missing evidence turns are adjacent to a selected turn. These descriptive
opportunities did not guarantee gains under a fixed budget.

Challenge data were authored before scoring. The initial freeze is `2610b36`;
pre-scoring review corrected one relation direction and completed the round-robin
one-hop contract in `3492e48`, without changing texts, gold or splits. Final corpus
SHA-256: `6f754015eebe6c9fba49deb6b67b1dd52dd48b7cf5dbbfc9418d9ae4cd32d7c9`.
Runner `9223352` was committed before the evaluation split or new external runs.
Model/fusion/seed quota/expansion rules were not tuned on their outcomes. See the
[summary](results/2026-09-19-history-summary.json) for code, data and artifact hashes.

The encoder remains pinned multilingual E5 small at
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, normalized on CPU, with the Hub offline.
Python 3.12.13, SQLite 3.50.4, Sentence Transformers 5.7.0, Transformers 5.17.0,
PyTorch 2.14.0 and NumPy 2.5.3. Development/evaluation splits each have 24 authored
cases across four distinct topics, three families and two languages. Translations
and templates are correlated and author-visible; this is **not an independent
blind holdout**. Identical dev/evaluation aggregates reflect shared template
structure and are not evidence of broad generalization.

## Authored challenge

Both splits produced the following results at five whole records / 4096 UTF-8
content bytes. Explicit links and time bounds are provided by the fixture, not
inferred by an agent. All systems first take an item-limited prefix and then skip
oversized records without refilling. All sixteen temporal cases across both splits
include known bounds; the baseline does not support these structured bounds.

| System | Dev mean recall | Dev complete coverage | Evaluation mean recall | Evaluation complete coverage |
| --- | ---: | ---: | ---: | ---: |
| Lexical | 22.22% | 0% | 22.22% | 0% |
| Lexical + time filter | 47.22% | 33.33% | 47.22% | 33.33% |
| Lexical + time + links | 58.33% | 50% | 58.33% | 50% |
| Lexical + time + session | 61.11% | 50% | 61.11% | 50% |
| Lexical + time + both | 66.67% | 66.67% | 66.67% | 66.67% |
| Hybrid | 27.78% | 0% | 27.78% | 0% |
| Hybrid + time filter | 50.69% | 33.33% | 50.69% | 33.33% |
| Hybrid + time + links | 61.81% | 50% | 61.81% | 50% |
| Hybrid + time + session | 64.58% | 50% | 64.58% | 50% |
| Hybrid + time + both | 70.14% | 66.67% | 70.14% | 66.67% |

Family breakdown for hybrid + both: linked 50%, session 50%, temporal 100%
complete coverage in each split. English complete coverage is 100%; Chinese is
33.33%, with mean recall 40.28%. The Chinese linked/session templates fail to
reliably place the relevant seed early enough; expansion cannot recover context
from a seed that was never selected. These failures remain in the corpus/score.
The gains demonstrate the use of supplied structure, not automatic understanding
of event dates, causes, or cross-session relationships.

## Unchanged public external workload

LoCoMo remains 5,882 turns, ten conversations, 1,986 questions, 1,527 scored and
459 excluded (446 adversarial, 4 empty evidence, 9 unresolved IDs). Same pinned
upstream revision, license and five-turn / 4096-byte ceiling as the
[previous report](2026-09-18-semantic-results.md). Import uses original session
order only: no gold-derived links, event timestamps, time windows, answers or
question labels enter indexing/ranking. All original five systems reproduce
**all 7,635 prior selections and quality scores exactly**.

| System | Mean Recall@5 | Multi-evidence recall | Complete multi-evidence |
| --- | ---: | ---: | ---: |
| Lexical / reference BM25 | 42.76% | 17.02% | 13/405 (3.21%) |
| Semantic | 48.24% | 25.09% | 33/405 (8.15%) |
| Hybrid | 51.90% | 26.04% | 35/405 (8.64%) |
| Lexical + session expansion | 46.91% | 18.35% | 21/405 (5.19%) |
| Hybrid + session expansion | 52.07% | 23.99% | 35/405 (8.64%) |

Relative to hybrid: 94 questions improve, 1,326 tie, **107 regress**. The 0.18
percentage-point overall gain comes with a 2.05-point multi-evidence recall
loss; single-evidence recall rises from 61.23% to 62.21%. This is not a multi-hop
retrieval breakthrough. It supports retaining normal hybrid search for ordinary
use and selecting structural expansion only when the application needs it.
No experimental configuration was changed after observing these outcomes.

The full trace includes recency and every regression. History variants select
five records before byte pruning and do not refill oversized items; original
baselines retain their original greedy whole-record budget policy. Both obey
identical ceilings, but this ordering is part of the algorithm difference.

Local p95 query latency: lexical 5.66 ms, lexical + session 12.29 ms, hybrid
22.15 ms, hybrid + session 22.53 ms. Later modes reuse earlier cached query
vectors; model/document preparation is excluded. These are one-machine
measurements, not standalone cold-start or throughput comparisons. No iid
confidence interval is asserted for questions clustered in ten conversations.

## Protocol and operational checks

The 349-test suite passes on MCP 1.2.0, 1.30.0 and 2.2.0. It includes migration
rollback, competing session-position claims, atomic link/context audits,
retirement, one-hop/cycle budgets, old database compatibility, strict MCP integer
arguments, frame/time/forgetting guards and mutations during encoder execution.
History protocol remains 120/120 episodes and 990/990 assertions; reliability
remains 6/6. Corrections invalidate dependent narratives; links never corroborate.

[Real cached-model MCP acceptance](results/2026-09-19-history-mcp.json) passes two
server processes, five searches and six checks: English/Chinese evidence,
chronological ordering with unknown-time exclusion, link retirement, withdrawal
with narrative review, and persistence after restart. These are scripted MCP
calls, not autonomous LLM tool-choice or answer-generation trials.

A fresh wheel installed outside the checkout also passes all 349 tests and the
actual-model MCP acceptance. Its 240 evaluation-split selections/quality rows
exactly match source. Repeating the full external evaluation from the installed
wheel reproduces all 10,689 selections/quality rows and scorer code hashes.
Wheel/sdist build checks confirm source bytes and exclude
model weights and external conversation data.

## Reproduce and inspect

Use the commands in the [API contract](../history-retrieval.md). Full traces:
[development](results/2026-09-19-history-development.json.gz),
[evaluation](results/2026-09-19-history-evaluation.json.gz),
[external](results/2026-09-19-history-external.json.gz),
[protocol](results/2026-09-19-history-protocol.json.gz), and
[summary/checksums](results/2026-09-19-history-summary.json).
External artifacts contain IDs/metrics, not upstream conversation text or answers;
LoCoMo retains its separate CC-BY-NC-4.0 license and attribution. Model weights
and external data are not shipped in the package. CI stays model-free.

Next quality work should target seed coverage (especially Chinese), selective
expansion that avoids displacing relevant evidence, and independently prepared
cross-session histories. New claims also need answer/abstention and autonomous
agent evaluation. This experiment does not establish industry leadership.
