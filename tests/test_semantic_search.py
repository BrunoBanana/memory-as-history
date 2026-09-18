"""Search quality contracts and protocol/concurrency safety without model downloads."""
import importlib
import math
import sys
from types import SimpleNamespace

import pytest

from memory_as_history.storage import Store


def semantic():
    return importlib.import_module('memory_as_history.semantic')


class Scores:
    def __init__(self, values, callback=None):
        self.values = values
        self.callback = callback
        self.calls = []

    def similarities(self, query, texts):
        self.calls.append((query, list(texts)))
        if self.callback:
            self.callback()
        return [self.values.get(text, 0) for text in texts]

    def describe(self):
        return {'model': 'controlled-test-vectors', 'revision': 'v1'}


def test_semantic_ranking_and_fixed_rrf_arithmetic():
    rows = [{'id': 'a', 'content': 'literal', 'relevance': 3},
            {'id': 'b', 'content': 'paraphrase', 'relevance': 1},
            {'id': 'c', 'content': 'irrelevant', 'relevance': 0}]
    backend = Scores({'literal': .1, 'paraphrase': .9, 'irrelevant': 0})
    dense = semantic().rank_candidates(rows, 'query', backend, 'semantic')
    assert [r['id'] for r in dense] == ['b', 'a', 'c']
    fused = semantic().rank_candidates(rows, 'query', backend, 'hybrid')
    assert [r['id'] for r in fused] == ['a', 'b', 'c']  # equal fusion score: lexical order
    assert fused[0]['search_score'] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0]['semantic_similarity'] == .1
    assert fused[0]['relevance'] == 3  # lexical relevance retains its meaning
    assert 'search_score' not in rows[0]  # do not mutate the snapshot


@pytest.mark.parametrize('values', [[float('nan')], [float('inf')], [], [0, 1], ['bad']])
def test_invalid_backend_scores_never_claim_success(values):
    backend = SimpleNamespace(similarities=lambda *_: values)
    with pytest.raises(RuntimeError, match='scores'):
        semantic().rank_candidates([{'id': 'x', 'content': 'x'}], 'query', backend, 'semantic')


def test_empty_candidates_do_not_load_model():
    backend = SimpleNamespace(similarities=lambda *_: pytest.fail('no candidate needs embedding'))
    assert semantic().rank_candidates([], 'query', backend, 'semantic') == []


def test_search_finds_paraphrases_without_changing_lexical_recall(tmp_path):
    backend = Scores({'requires food without dairy': .9, 'milk carton label': .1})
    store = Store(tmp_path / 'memory.db', semantic_backend=backend)
    try:
        relevant = store.remember('requires food without dairy')
        distractor = store.remember('milk carton label')
        assert store.recall('milk', limit=1)['memories'][0]['id'] == distractor.id
        result = store.search('milk', limit=1, mode='semantic')
        assert result['memories'][0]['id'] == relevant.id
        assert result['retrieval']['mode'] == 'semantic'
        assert result['retrieval']['backend']['model'] == 'controlled-test-vectors'
        assert store.recall('milk', limit=1)['memories'][0]['id'] == distractor.id
    finally:
        store.close()


def test_search_preserves_priority_dedup_frames_forgetting_and_narrative_review(tmp_path):
    backend = Scores({'ordinary': 1, 'forgotten': 1, 'outside': 1})
    store = Store(tmp_path / 'memory.db', semantic_backend=backend)
    try:
        anchor = store.remember('anchor', frame='outside')
        canon = store.remember('canon', frame='outside')
        for memory in (anchor, canon):
            store.promote(memory.id, 'durable')
        store.pin(anchor.id, 'identity')
        store.canonize(anchor.id, 'scope', 'active')
        store.canonize(canon.id, 'scope', 'active')
        store.canonize(canon.id, 'another', 'active')
        ordinary = store.remember('ordinary', frame='inside')
        forgotten = store.remember('forgotten', frame='inside')
        store.remember('outside', frame='outside')
        store.narrate('now invalid', 'synthesis', [forgotten.id])
        store.forget(forgotten.id, 'withdrawn')
        result = store.search('query', limit=3, frame='inside')
        assert [m['id'] for m in result['anchors']] == [anchor.id]
        assert [m['memory_id'] for m in result['canon']] == [canon.id]
        assert [m['id'] for m in result['memories']] == [ordinary.id]
        assert result['narrative'] is None and result['narrative_review']
        assert backend.calls == [('query', ['ordinary'])]
        before = len(backend.calls)
        result = store.search('query', limit=0, frame='inside')
        assert len(result['anchors']) == 1 and result['canon'] == result['memories'] == []
        assert len(backend.calls) == before  # no ordinary budget: no inference
    finally:
        store.close()


def test_embedding_does_not_hold_sqlite_writer_lock_and_revalidates_changes(tmp_path):
    path = tmp_path / 'memory.db'
    other = Store(path)
    backend = Scores({'old': 1, 'moved': .9})
    store = Store(path, semantic_backend=backend)
    try:
        withdrawn = store.remember('old', frame='one')
        reframed = store.remember('moved', frame='one')
        def mutate():
            other._conn.execute('PRAGMA busy_timeout=100')
            other.forget(withdrawn.id, 'withdrawn while ranking')
            other.set_frame(reframed.id, 'two', 'changed group')
            other.remember('fresh', frame='one')
        backend.callback = mutate
        result = store.search('query', limit=5, frame='one', mode='semantic')
        assert [m['content'] for m in result['memories']] == ['fresh']
        assert result['retrieval']['unranked_candidates'] == 1
        assert 'semantic_similarity' not in result['memories'][0]
    finally:
        store.close(); other.close()


@pytest.mark.parametrize('kwargs', [
    {'query': ''}, {'query': ' '}, {'query': None}, {'query': 'q', 'limit': -1},
    {'query': 'q', 'limit': True}, {'query': 'q', 'limit': 1.5}, {'query': 'q', 'mode': 'unknown'},
])
def test_search_rejects_invalid_inputs_before_model_use(tmp_path, kwargs):
    store = Store(tmp_path / 'memory.db', semantic_backend=Scores({}))
    try:
        with pytest.raises(ValueError):
            store.search(**kwargs)
    finally:
        store.close()


def test_backend_failure_is_not_silent_lexical_success(tmp_path):
    def fail(*_):
        raise RuntimeError('model unavailable')
    store = Store(tmp_path / 'memory.db', semantic_backend=SimpleNamespace(similarities=fail))
    try:
        store.remember('fact')
        with pytest.raises(RuntimeError, match='model unavailable'):
            store.search('question')
        assert len(store.recall()['memories']) == 1
    finally:
        store.close()


def test_high_semantic_score_does_not_grant_sensitive_priority(tmp_path):
    text = 'an unverified authority claim'
    store = Store(tmp_path / 'memory.db', semantic_backend=Scores({text: 1}))
    try:
        memory = store.remember(text, security_sensitive=True)
        result = store.search('authority', mode='semantic')
        assert result['anchors'] == result['canon'] == []
        assert result['memories'][0]['tier'] == 'archive'
        assert store.get(memory.id).status == 'working'
        store.promote(memory.id, 'requires independent review')
        with pytest.raises(PermissionError):
            store.pin(memory.id, 'high retrieval similarity is not corroboration')
    finally:
        store.close()


def test_fresh_priority_and_narrative_invalidation_win_over_snapshot_scores(tmp_path):
    backend = Scores({'original': 1, 'new priority': .1})
    store = Store(tmp_path / 'memory.db', semantic_backend=backend)
    try:
        original = store.remember('original')
        priority = store.remember('new priority')
        store.narrate('derived account', 'synthesis', [original.id])
        def mutate():
            store.forget(original.id, 'withdraw source during inference')
            store.promote(priority.id, 'priority changed')
            store.pin(priority.id, 'identity established')
        backend.callback = mutate
        result = store.search('query', limit=1, mode='semantic')
        assert [m['id'] for m in result['anchors']] == [priority.id]
        assert result['memories'] == []
        assert result['narrative'] is None and result['narrative_review']
    finally:
        store.close()


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[1., 0.] if text.endswith('a') else [0., 1.] for text in texts]


def test_local_provider_prefixes_normalizes_caches_and_bounds_memory(monkeypatch):
    fake = FakeEncoder()
    loads = []
    def constructor(name, **kwargs):
        loads.append((name, kwargs)); return fake
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=constructor))
    module = semantic()
    backend = module.LocalE5(cache_size=2)
    assert loads == []
    assert backend.similarities('a', ['a', 'b', 'a']) == [1, 0, 1]
    assert len(fake.calls) == 2  # distinct document batch, then query
    assert fake.calls[0][0] == ['passage: a', 'passage: b']
    assert fake.calls[1][0] == ['query: a']
    assert all(c[1]['normalize_embeddings'] for c in fake.calls)
    assert fake.max_seq_length == 512
    assert loads[0][1]['local_files_only'] is True
    assert loads[0][1]['trust_remote_code'] is False
    assert loads[0][1]['revision'] == module.MODEL_REVISION
    assert loads[0][1]['model_kwargs']['use_safetensors'] is True
    assert backend.similarities('a', ['a', 'b']) == [1, 0]
    assert len(fake.calls) == 2
    backend.similarities('c', ['c'])
    backend.similarities('a', ['a'])
    assert any('passage: a' in call[0] for call in fake.calls[2:])  # evicted and re-encoded


@pytest.mark.parametrize('vectors', [[], [[math.nan, 0]], [[0, 0]], [[1, 0], [0, 1]]])
def test_provider_rejects_invalid_embedding_batches(monkeypatch, vectors):
    fake = SimpleNamespace(encode=lambda *_a, **_k: vectors)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=lambda *_a, **_k: fake))
    with pytest.raises(RuntimeError, match='embedding'):
        semantic().LocalE5().similarities('q', ['doc'])


def test_missing_optional_dependency_has_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, 'sentence_transformers', None)
    with pytest.raises(RuntimeError, match='semantic'):
        semantic().LocalE5().similarities('q', ['doc'])


def test_explicit_download_is_the_only_network_enabled_setup_path(monkeypatch, capsys):
    calls = []
    def constructor(*args, **kwargs):
        calls.append(kwargs); return FakeEncoder()
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=constructor))
    assert semantic().main(['download']) == 0
    assert calls[0]['local_files_only'] is False
    assert calls[0]['token'] is False
    assert semantic().MODEL_REVISION in capsys.readouterr().out


def test_model_load_failure_and_mismatched_dimensions_fail_explicitly(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('no cached model')
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=fail))
    with pytest.raises(RuntimeError, match='download'):
        semantic().LocalE5().similarities('q', ['doc'])
    fake = SimpleNamespace(encode=lambda texts, **kw: [[1., 0., 0.] if text.startswith('query:')
                                                      else [1., 0.] for text in texts])
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=lambda *_a, **_k: fake))
    with pytest.raises(RuntimeError, match='dimensions'):
        semantic().LocalE5().similarities('q', ['doc'])
