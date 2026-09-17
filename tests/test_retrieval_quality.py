"""Numerical and input-boundary regressions for the BM25 retrieval contract."""
import math

import pytest

from memory_as_history.storage import Store, _bm25_scores, _tokenize


def test_bm25_uses_logarithmic_idf_with_independently_calculated_score():
    # N=4, df(alpha)=2, tf=2, dl=3, avgdl=1.5, k1=1.5, b=.75.
    expected = math.log(2) * 5 / 4.625
    assert _bm25_scores(['alpha'], ['alpha', 'alpha', 'beta'], 1.5, {'alpha': 2}, 4) == pytest.approx(expected)


def test_bm25_preserves_average_lengths_below_one():
    # One one-token row and three empty rows: avgdl=.25, not 1.
    expected = math.log(1 + 3.5 / 1.5) * 2.5 / (1 + 1.5 * (.25 + .75 * 4))
    assert _bm25_scores(['alpha'], ['alpha'], .25, {'alpha': 1}, 4) == pytest.approx(expected)


def test_repeating_query_terms_does_not_artificially_multiply_score():
    args = (['alpha', 'beta'], 2, {'alpha': 1, 'beta': 1}, 2)
    assert _bm25_scores(['alpha', 'alpha'], *args) == _bm25_scores(['alpha'], *args)


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / 'ranking.db')
    yield instance
    instance.close()


@pytest.mark.parametrize('query', ['', 'the and is', '!!!', 'unmatched'])
def test_nonmatching_query_has_zero_scores_and_preserves_default_order(store, query):
    early = store.remember('early preference')
    store.remember('recent preference')
    store.promote(early.id, 'durable')
    default = store.recall()['memories']
    queried = store.recall(query=query)['memories']
    assert [r['id'] for r in queried] == [r['id'] for r in default]
    assert all(r['relevance'] == 0 for r in queried)


@pytest.mark.parametrize('cached', ['{broken', 'null', '42', '{}', '[{}]', '[1]', '"alpha"'])
def test_malformed_legacy_cache_falls_back_to_original_text(store, cached):
    memory = store.remember('alpha beta')
    store.remember('other unrelated terms')
    before = store.recall(query='alpha')['memories']
    store._conn.execute('UPDATE memories SET content_tokens=? WHERE id=?', (cached, memory.id))
    store._conn.commit()
    after = store.recall(query='alpha')['memories']
    assert [(r['id'], r['relevance']) for r in after] == [(r['id'], r['relevance']) for r in before]


def test_tokenizer_preserves_frequencies_and_mixed_script_boundaries():
    assert _tokenize('SQLite SQLite，记忆历史！') == ['sqlite', 'sqlite', '记忆', '忆历', '历史']
    assert _tokenize('The ALPHA beta42 under_score') == ['alpha', 'beta42', 'under_score']


def test_query_respects_frame_and_forgetting_while_retaining_priority(store):
    hidden = store.remember('alpha repeated', frame='private')
    store.forget(hidden.id, 'withdrawn')
    store.remember('alpha beta', frame='other')
    ordinary = store.remember('alpha gamma', frame='selected')
    anchor = store.remember('unrelated identity', frame='other')
    store.promote(anchor.id, 'durable')
    store.pin(anchor.id, 'identity')
    result = store.recall(query='alpha', frame='selected', limit=2)
    assert [r['id'] for r in result['anchors']] == [anchor.id]
    assert [r['id'] for r in result['memories']] == [ordinary.id]
