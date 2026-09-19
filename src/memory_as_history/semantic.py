"""Optional local semantic ranking. Importing this module loads no model."""
from collections import OrderedDict
import hashlib
from importlib.metadata import PackageNotFoundError, version
import math
import threading

MODEL_ID = 'intfloat/multilingual-e5-small'
MODEL_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
RRF_K = 60


def rank_candidates(rows, query, backend, mode):
    """Rank a lexical-order snapshot without altering its eligibility or rows."""
    if mode not in ('semantic', 'hybrid'):
        raise ValueError('mode must be semantic or hybrid')
    if not rows:
        return []
    raw = backend.similarities(query, [row['content'] for row in rows])
    try:
        scores = [float(value) for value in raw]
        if len(scores) != len(rows) or not all(math.isfinite(value) for value in scores):
            raise ValueError('nonfinite or wrong number of scores')
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError('Semantic backend returned invalid scores') from exc
    semantic_order = sorted(range(len(rows)), key=lambda index: -scores[index])
    ranks = {index: rank for rank, index in enumerate(semantic_order, 1)}
    combined = [scores[i] if mode == 'semantic' else
                1 / (RRF_K + i + 1) + 1 / (RRF_K + ranks[i]) for i in range(len(rows))]
    return [{**rows[i], 'semantic_similarity': scores[i], 'search_score': combined[i]}
            for i in sorted(range(len(rows)), key=lambda index: -combined[index])]


class LocalE5:
    """Pinned E5, local-only by default; bounded process-local derived vectors.

    Download explicitly with `python -m memory_as_history.semantic download`.
    The cache stores text hashes and vectors, not source text, and never supplies
    candidate IDs. Eligibility always comes from a fresh Store recall.
    """
    def __init__(self, device='cpu', cache_size=10000, allow_download=False):
        if type(cache_size) is not int or cache_size < 1:
            raise ValueError('cache_size must be a positive integer')
        self.device = device
        self.cache_size = cache_size
        self.allow_download = allow_download
        self._model = None
        self._documents = OrderedDict()
        self._queries = OrderedDict()
        self._lock = threading.RLock()

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError('Install memory-as-history[semantic] and run '
                                   'python -m memory_as_history.semantic download') from exc
            try:
                model = SentenceTransformer(
                    MODEL_ID, revision=MODEL_REVISION, device=self.device,
                    local_files_only=not self.allow_download, trust_remote_code=False,
                    token=False, model_kwargs={'use_safetensors': True},
                )
                model.max_seq_length = 512
                self._model = model
            except Exception as exc:
                raise RuntimeError('Local semantic model unavailable. Run '
                                   'python -m memory_as_history.semantic download; '
                                   'normal search never downloads a model.') from exc
        return self._model

    def _encode(self, texts):
        try:
            encoded = self._load().encode(texts, batch_size=32, normalize_embeddings=True,
                                          convert_to_numpy=True, show_progress_bar=False, prompt='')
            vectors = [[float(value) for value in row] for row in encoded]
            if len(vectors) != len(texts):
                raise ValueError('wrong embedding count')
            normalized = []
            for vector in vectors:
                if not vector or not all(math.isfinite(v) for v in vector):
                    raise ValueError('invalid embedding values')
                norm = math.sqrt(math.fsum(v * v for v in vector))
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError('invalid embedding norm')
                normalized.append(tuple(v / norm for v in vector))
            if len({len(row) for row in normalized}) > 1:
                raise ValueError('inconsistent embedding dimensions')
            return normalized
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError('Semantic encoder returned invalid embeddings') from exc

    def _vectors(self, texts, prefix, cache, capacity):
        keys = [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in texts]
        missing = {}
        available = {}
        for key, text in zip(keys, texts):
            if key in cache:
                available[key] = cache[key]
                cache.move_to_end(key)
            else:
                missing[key] = text
        if missing:
            vectors = self._encode([prefix + text for text in missing.values()])
            for key, vector in zip(missing, vectors):
                available[key] = cache[key] = vector
            while len(cache) > capacity:
                cache.popitem(last=False)
        return [available[key] for key in keys]

    def prepare_documents(self, texts):
        """Optional batch warm-up, used by benchmarks before the timed queries."""
        with self._lock:
            self._vectors(texts, 'passage: ', self._documents, self.cache_size)

    def similarities(self, query, texts):
        with self._lock:
            documents = self._vectors(texts, 'passage: ', self._documents, self.cache_size)
            vector = self._vectors([query], 'query: ', self._queries, 32)[0]
            if any(len(document) != len(vector) for document in documents):
                raise RuntimeError('Semantic encoder returned incompatible embedding dimensions')
            return [math.fsum(a * b for a, b in zip(vector, document)) for document in documents]

    def describe(self):
        dependencies = {}
        for name in ('sentence-transformers', 'transformers', 'torch', 'numpy'):
            try:
                dependencies[name] = version(name)
            except PackageNotFoundError:
                dependencies[name] = None
        return {'model': MODEL_ID, 'revision': MODEL_REVISION, 'device': self.device,
                'max_sequence_tokens': 512, 'query_prefix': 'query: ', 'document_prefix': 'passage: ',
                'normalized': True, 'local_files_only': not self.allow_download,
                'document_cache_capacity': self.cache_size, 'dependencies': dependencies}


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Explicitly download the pinned optional local encoder.')
    parser.add_argument('command', choices=['download'])
    parser.parse_args(argv)
    backend = LocalE5(allow_download=True)
    backend.prepare_documents(['Local model setup verification.'])
    print(json.dumps(backend.describe(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
