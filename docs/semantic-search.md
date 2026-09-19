# Optional local semantic evidence search

`search(query, mode="hybrid")` finds evidence by meaning as well as wording.
`recall()` keeps its existing model-free lexical behavior. Both use the same
history protocol: anchors first, then active canon, then ordinary memories;
forgetting, frame restrictions and narrative review remain in effect.

## Setup and use

Run setup in the Python environment that launches the MCP server. From a checkout:

```sh
python -m pip install -e '.[semantic]'
python -m memory_as_history.semantic download
```

The first command installs optional Sentence Transformers/PyTorch dependencies;
the second explicitly downloads the pinned multilingual encoder to the normal
Hugging Face cache. Models are not included in the wheel. Ordinary installation
does not install this extra, and ordinary recall does not load an encoder.

Call the new MCP tool `search` with arguments such as:

```json
{"query":"What seating and meal accommodations were requested?","mode":"hybrid","limit":5,"frame":"travel"}
```

Python callers can use:

```python
from memory_as_history.storage import Store

store = Store("memory.db")
try:
    result = store.search("What changed about our deployment requirements?",
                          limit=5, mode="hybrid")
finally:
    store.close()
```

Supported modes:

- `semantic`: normalized cosine similarity from the local encoder.
- `hybrid` (default): equal reciprocal-rank fusion of BM25 and semantic order,
  `1 / (60 + lexical_rank) + 1 / (60 + semantic_rank)`, with one-based ranks.
  Ties preserve lexical order. Every eligible ordinary candidate participates.

`query` must be nonempty; `limit` must be a nonnegative integer. Anchors retain
their existing exception and can exceed the limit. `frame` restricts ordinary
memories, as it does in recall. Canon and anchors retain their shared/global
scope. Extra narrative, review and conflict sections keep their existing rules
and are not included in that item limit.

Returned ordinary rows retain lexical `relevance` and add `semantic_similarity`
and `search_score` when ranked. Neither score is a probability, factuality rating
or provenance upgrade. The `retrieval` field records mode, model/revision,
dependency versions, inference status and ranked/unranked candidate counts.

## Model and runtime contract

The encoder is [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)
at revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, licensed MIT. The implementation
follows the model card's `query: ` / `passage: ` prefixes, normalization and
512-token input limit, including for non-English text. Long memories remain
intact in storage/output, but the encoder sees only its token-limited input.
Lexical recall remains available over the full stored content.

Normal search loads only cached model files, uses safetensors, disables remote
model code and performs inference locally on CPU by default. Missing dependencies
or weights yield an actionable error; there is no silent lexical fallback that
reports semantic success. Empty ordinary candidate sets or a budget consumed by
priority memories need no inference and report `inference_performed=false`.

The in-process cache holds up to 10,000 document vectors and 32 query vectors,
keyed by content hashes. It stores no raw text and adds no database tables. It
is an optimization, never the source of eligible memory IDs. Restarting the
server discards it and re-encodes eligible content on demand. Forgetting is still
the protocol's accountable tombstone, not secure erasure of historical text or
immediate purging of all derived cache entries.

Custom Python integrations can provide `Store(..., semantic_backend=backend)`.
The backend implements `similarities(query, texts)` (one finite score per text)
and `describe()` (JSON-compatible provenance). Built-in benchmarks also call
`prepare_documents(texts)` to time document preparation separately. Custom
backends are application code; the built-in provider's local-only guarantee does
not govern a caller-supplied provider.

## Concurrency and historical authority

Search takes a lexical candidate snapshot, releases its transaction, then runs
the encoder. It subsequently reads the current history view again. A concurrent
withdrawal, frame change, canon update or anchor change takes effect before
results are assembled. Ranked IDs no longer eligible are discarded; changed
content does not inherit an earlier semantic score. Current narrative dependency
checks are also reapplied. Both reads use the established transaction mechanism;
encoding never holds its SQLite writer lock.

Newly eligible or changed memories follow surviving ranked rows in fresh lexical
order. They are counted as `unranked_candidates` and encoded on the next search.
This defines behavior during concurrent changes without blocking writers for
model computation. The returned result reflects the final read's snapshot;
subsequent changes naturally require another read.

Semantic similarity never calls promote, corroborate, pin, canonize or narrate.
A highly ranked unverified claim remains an unverified claim. Existing source
gates still apply to any later priority or narrative operation.

## Reproduce quality and acceptance checks

```sh
python -m memory_as_history.benchmarks development --semantic --output development.json
python -m memory_as_history.benchmarks locomo --semantic \
  --data .benchmark-data/locomo10.json --output semantic-locomo.json
python scripts/semantic_acceptance.py --output semantic-acceptance.json
```

Use the [pinned external download instructions](benchmarks/README.md) first.
The development corpus is packaged, hash-checked and author-visible; it is not
a blind holdout. The acceptance script uses actual cached-model inference across
two MCP server processes and a temporary synthetic database, with no hosted LLM.
It checks English/Chinese paraphrases and forgetting before/after restart.

CI exercises controlled scores, cache/input/error contracts, live SQLite races
and actual MCP schemas without model downloads. It also preserves the lexical
development report. Actual model scores and repeated local runs are published
in the [semantic results report](benchmarks/2026-09-18-semantic-results.md).
These retrieval measurements do not measure generated answers, source
authentication, autonomous tool selection or behavior on unseen user histories.
