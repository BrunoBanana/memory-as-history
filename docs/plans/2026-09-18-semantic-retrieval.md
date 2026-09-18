# Optional Semantic Evidence Search Implementation Plan

**Goal:** Improve retrieval of paraphrased and multiple evidence turns while
preserving the existing history protocol, with reproducible before/after data.

**Architecture:** Add an explicit `search` API alongside lexical `recall`. A
local multilingual encoder can rank ordinary eligible memories by cosine
similarity or fuse semantic and lexical ranks. Inference runs outside SQLite
write transactions; a fresh protocol recall revalidates eligibility and priority
before returning. Default installation and ordinary recall remain model-free.

**Tech Stack:** Python/SQLite/MCP, optional Sentence Transformers + PyTorch,
pinned multilingual-e5-small, existing benchmark adapter and pytest.

The user approved proceeding with multi-evidence retrieval improvement and has
asked for autonomous implementation, testing, refinement and integration. This
document records the design before implementation; no additional approval gate
or separate agent is needed. Use the existing global worktree convention and
continue in one flow through exact-head CI, merge and main verification.

## Alternatives and scope

1. Keyword rewrites/diversification: inexpensive but no general semantic bridge.
2. Local semantic + lexical fusion: selected; measurable without hosted services,
   works for English/Chinese and adds no mandatory model dependency.
3. Neighbor expansion: defer until explicit conversation/session boundaries exist
   in the storage contract; chronological adjacency alone is not reliable linkage.

This round does not add semantic source authentication, answer generation,
automatic corroboration, context-window enlargement or changes to history-v1.
LoCoMo has already been inspected and remains a public regression/reference set,
not a blind holdout. Synthetic development examples are author-visible too.

## API and invariants

- `Store.search(query, limit=10, frame=None, mode="hybrid")` and MCP `search`
  return the existing recall sections plus explicit retrieval metadata.
- Modes are `semantic` (cosine) and `hybrid` (equal reciprocal-rank fusion,
  k=60, ranks start at 1). Ties preserve lexical order. These are fixed before
  external scoring; compare both without tuning on LoCoMo labels.
- Anchors, active canon, frame eligibility, forgotten rows, narrative review and
  conflicts retain the ordinary recall rules. Scores cannot grant priority.
- First obtain ordinary eligible candidates using public recall. Embed/rank
  outside its transaction. Obtain a fresh recall before returning; discard
  withdrawn, reframed or changed candidates and respect current priority/budget.
  Newly eligible/changed rows fall back to fresh lexical order after ranked rows,
  counted in metadata. Concurrent new text is embedded on the next query.
- No silent fallback on missing models or invalid/nonfinite scores. Return an
  actionable setup/runtime error. A failed search must not claim semantic success.
- The encoder is lazy and local-files-only for normal search. Explicit setup
  command downloads a fixed model revision using safetensors and no remote code.
  Query/document prefixes follow E5's model card; vectors are normalized.
  Derived document vectors use a bounded in-memory cache, with no new DB schema.
  No query or memory text is sent to a hosted inference service.
- Encoder revision: `intfloat/multilingual-e5-small` at
  `614241f622f53c4eeff9890bdc4f31cfecc418b3` (MIT). Record dependency versions,
  model settings, preparation costs and warm/cold timing limitations.

## Task 1: freeze development data and establish baseline

Files: `src/memory_as_history/benchmarks/data/semantic-dev-v1.json` and manifest;
`docs/plans/task.md`. Author English/Chinese paraphrase, exact identifier,
multi-evidence and dated-fact development cases with distractors and explicit
expected IDs. Commit data/hash before encoding/scoring. Run all 267 baseline
tests in the isolated worktree. Data generator/report must not inspect LoCoMo
answers to construct the development workload.

## Task 2: semantic ranking and safe local provider (TDD)

Files: `src/memory_as_history/semantic.py`, `tests/test_semantic_search.py`,
`pyproject.toml`. Write failing tests for ranking, fixed RRF arithmetic/ties,
nonfinite/wrong-length scores, optional dependency errors, explicit download,
prefixes, normalization and cache bounds. Implement the minimum provider and
ranker; tests use controlled vectors without downloading a model in CI.
Run tests, then commit. Install the semantic extra in a separate local environment
and download the pinned model for the actual development/external experiments.

## Task 3: Store/MCP search and concurrency (TDD)

Files: `src/memory_as_history/storage.py`, `src/memory_as_history/server.py`,
`tests/test_semantic_search.py`, `tests/test_server.py`.
Write failing tests for paraphrase ordering, priority/global budget, forgotten
and framed content, stale narrative suppression, invalid inputs and actual stdio
schema/error behavior. Make a second connection write while the backend runs;
assert it succeeds and a subsequently forgotten/reframed row is absent.
Implement search with two short recall transactions and no model work in either.
Verify failure modes, full suite and unchanged 990 history protocol assertions.

## Task 4: evaluate against the frozen baselines

Files: `src/memory_as_history/benchmarks/retrieval.py`, `__main__.py`,
`common.py`, `tests/test_benchmarks.py`, `docs/benchmarks/`.
Add optional semantic/hybrid runs, per-evidence-count metrics, model provenance
and the packaged development command. Preserve the original three-system CLI
and historical artifacts. Tests must prove label isolation, equal byte/item
budgets, exclusions/denominators and disabled/invalid-backend failures.
Measure development first, then pinned LoCoMo with unchanged preprocessing,
five-turn/4096-byte budgets, gold IDs and exclusions. Publish all systems' results,
including regressions, multi-evidence coverage, cost/latency and limitations.
No promised target percentage and no retrospective claim of blind validation.

## Task 5: compatibility, packaging and delivery

Update bilingual README/setup, protocol docs, evaluation scope and changelog.
Run full tests on three MCP versions, the existing reliability/mechanism checks,
history-v1, clean wheel installation and actual local-model smoke tests. Validate
report hashes and recompute scores from traces. Keep the model and external data
outside Git/wheels. Create PR, require all nine checks on its exact head, merge,
sync local main and verify main tests/CI. Do not publish to PyPI.

Sources: [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md),
[SentenceTransformer API](https://sbert.net/docs/package_reference/sentence_transformer/model.html).
