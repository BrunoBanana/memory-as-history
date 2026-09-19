# Optional semantic search results — 2026-09-18

On the unchanged public LoCoMo evidence-retrieval workload, optional hybrid
search raises mean Recall@5 from **42.76% to 51.90%**. For the 405 questions
requiring multiple evidence turns, it rises from **17.02% to 26.04%**. This is
a measured improvement on a previously inspected reference set, not a blind
test, official LoCoMo answer score or competing-product leaderboard.

## Fixed experiment and provenance

Implementation/runner commit: `15ff9a3` (full SHA and file hashes in the
[summary](results/2026-09-18-semantic-summary.json)). The original
[lexical report](2026-09-18-results.md) and traces remain unchanged. All three
original systems reproduce their earlier selections and quality scores.

The same 5,882 turns, ten conversations, 1,986 original questions, 1,527 eligible
questions and 459 exclusions are used: 446 adversarial, 4 without evidence and
9 with unresolved IDs. Preprocessing, captions, gold IDs, exact-label policy and
five-whole-turn / 4096-UTF-8-content-byte budgets are unchanged. Answers and
evidence labels never enter indexing, encoding or ranking. No privileged
anchor/canon annotations are supplied. See the [benchmark rules](README.md).

The encoder is [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)
at revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` (MIT), with E5 query/passage
prefixes, normalized vectors and a 512-token limit. Semantic mode uses cosine
similarity; hybrid uses equal reciprocal-rank fusion, k=60, with BM25. Both
configurations were recorded before external scoring and were not tuned on
LoCoMo outcomes. CPU inference ran locally with the Hub forced offline.

Environment: Python 3.12.13, macOS 26.6.2 arm64, SQLite 3.50.4, package 1.2.0rc1,
Sentence Transformers 5.7.0, Transformers 5.17.0, PyTorch 2.14.0, NumPy 2.5.3.
The model and external data are downloaded separately and are absent from the
project wheel. LoCoMo retains its separate [CC-BY-NC-4.0 license](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/LICENSE.txt)
and [upstream attribution](../../src/memory_as_history/benchmarks/data/locomo.manifest.json).

## External evidence results

| System | Mean Recall@5 | Hit@5 | MRR@5 | Multi-evidence recall |
| --- | ---: | ---: | ---: | ---: |
| Ordinary lexical recall | 42.76% | 47.61% | 0.3337 | 17.02% |
| Reference BM25 | 42.76% | 47.61% | 0.3337 | 17.02% |
| Recency | 0.18% | 0.26% | 0.0011 | 0.19% |
| Semantic search | 48.24% | 54.29% | 0.3931 | 25.09% |
| Hybrid search | **51.90%** | **57.83%** | **0.4234** | **26.04%** |

Hybrid's gain over lexical recall is 9.14 percentage points overall and 9.02
points on multi-evidence questions. On the 1,122 single-evidence questions,
recall rises from 52.05% to 61.23%. Among 405 multi-evidence questions, complete
evidence coverage rises from 13/405 (3.21%) to 35/405 (8.64%). Partial recall
must not be confused with retrieving every fact needed to answer a question.

| Paired question recall vs lexical | Improved | Tied | Regressed |
| --- | ---: | ---: | ---: |
| Semantic | 304 | 1,031 | 192 |
| Hybrid | 245 | 1,213 | 69 |

All regressions remain in the denominator and trace. Hybrid does not dominate
every query; lexical recall remains available and unchanged. By upstream
category IDs 1/2/3/4, hybrid recall is 24.25% / 62.06% / 24.88% / 60.04%.
Each of the ten conversations improves in mean recall, by 4.53–13.81 percentage
points. Per-conversation results are published; related questions are not
independent trials, so no iid confidence interval or population-wide claim is
attached to the 1,527-question aggregate.

## Development findings and remaining gap

The new development corpus was frozen in `79a9355` before model scoring:
64 documents and 32 questions, with eight authored topics and correlated
English/Chinese translations. Its SHA-256 is
`c3a5f50a8f28e725a9147ef1bfefc48b40d0dbd785729bb0d927a4dbc2d3108e`.

At the same five-turn budget, lexical, semantic and hybrid all achieve 100%
recall. MRR differs: 0.8099, 1.0000 and 0.9063. This workload is too easy to
separate multi-evidence recall and is not a blind holdout. We retained it and
its saturated results instead of rewriting the expectations after scoring.
No hyperparameter sweep or model selection followed the external results.

The original gold-budget ceiling is still 99.50%; hybrid multi-evidence recall
of 26.04% leaves substantial room for improvement. Next work should use harder,
independently prepared histories with explicit temporal/session boundaries,
then test linked-evidence expansion and date-aware retrieval. Source reliability,
answer generation, correct abstention and unguided tool choice need separate
evaluation. Potential overlap with encoder training data has not been audited.

## Runtime cost and protocol safety

Document preparation took 17.87 seconds over all ten conversations, including
initial model loading; it is separate from query latency. Recorded local query
p50/p95 times were:

| System | p50 ms | p95 ms |
| --- | ---: | ---: |
| Lexical recall | 4.75 | 5.36 |
| Reference BM25 | 0.65 | 0.90 |
| Semantic search | 26.21 | 27.55 |
| Hybrid search | 19.79 | 20.92 |

Hybrid runs after semantic and reuses its cached query vector, so this table
does **not** establish that standalone hybrid queries are cheaper than semantic
queries. Both semantic modes use two SQLite reads and a bounded in-memory vector
cache; the reference BM25 ranker is in-memory. These are single-machine timings,
not service throughput or cold-start guarantees. There is no persistent vector
index or large-history scaling claim.

All 120 history episodes and 990 protocol checks remain satisfied. New tests
verify priority budgets, forgotten/frame filtering, narrative invalidation,
source guards and writer access during encoding. A final fresh read discards
withdrawn/reframed candidates; newly eligible candidates are identified as not
yet semantically ranked. Scores never promote or corroborate memory.

The [actual local-model MCP acceptance](results/2026-09-18-semantic-mcp.json)
uses two fresh server processes and four search calls: English hybrid retrieval,
Chinese semantic retrieval, cached retrieval after withdrawal and retrieval after
restart. All checks pass. These are scripted MCP calls, not autonomous LLM trials.

## Reproduce and inspect

Follow [semantic setup](../semantic-search.md), then run:

```sh
python -m memory_as_history.benchmarks development --semantic --output development.json
python -m memory_as_history.benchmarks locomo --semantic \
  --data .benchmark-data/locomo10.json --output semantic-locomo.json
python scripts/semantic_acceptance.py --output semantic-acceptance.json
```

The [summary and artifact hashes](results/2026-09-18-semantic-summary.json) accompany
complete [external](results/2026-09-18-semantic-locomo.json.gz),
[development](results/2026-09-18-semantic-development.json.gz) and
[protocol](results/2026-09-18-semantic-protocol.json.gz) JSON traces. External traces
contain IDs/measurements, not upstream text, questions or answers. Repeated CPU
runs produced the same selected IDs, budget counts and quality scores for all
7,635 external rows (1,527 questions × 5 systems). Timings vary.

The implementation plan records compatibility and installed-wheel validation.
CI remains model-free and checks actual MCP transport plus deterministic provider
contracts; the real cached-model experiment is a separately recorded local run.
