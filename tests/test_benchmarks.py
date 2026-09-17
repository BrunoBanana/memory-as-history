"""Benchmark validity: score arithmetic, missing output, fairness and broken-system controls."""
import copy
import hashlib
import importlib
import json
from collections import Counter

import pytest

from memory_as_history.storage import Store


def module(name):
    return importlib.import_module(f'memory_as_history.benchmarks.{name}')


def test_frozen_corpus_has_120_cases_and_keeps_translations_in_one_split():
    protocol = module('protocol')
    corpus = protocol.load_corpus()
    cases = corpus['cases']
    assert len(cases) == len({c['id'] for c in cases}) == 120
    assert len({c['family'] for c in cases}) == 12
    assert Counter(c['language'] for c in cases) == {'en': 60, 'zh': 60}
    assert Counter(c['split'] for c in cases) == {'dev': 72, 'test': 48}
    for case in cases:
        assert sum(step['op'] == 'reopen' for step in case['steps']) >= 2
        assert len({c['split'] for c in cases if (c['family'], c['variant']) == (case['family'], case['variant'])}) == 1


@pytest.mark.parametrize('mutation', ['duplicate', 'empty', 'operation', 'assertion', 'reference'])
def test_invalid_corpus_is_rejected_instead_of_silently_skipped(mutation):
    protocol = module('protocol')
    corpus = copy.deepcopy(protocol.load_corpus())
    if mutation == 'duplicate':
        corpus['cases'].append(corpus['cases'][0])
    elif mutation == 'empty':
        corpus['cases'] = []
    elif mutation == 'operation':
        corpus['cases'][0]['steps'][0]['op'] = 'execute_arbitrary_sql'
    elif mutation == 'assertion':
        corpus['cases'][0]['steps'][2]['assertions'][0]['op'] = 'ignore'
    else:
        corpus['cases'][0]['steps'][3]['args']['memory_id'] = '$missing'
    with pytest.raises(ValueError):
        protocol.validate_corpus(corpus)


def test_missing_output_never_passes_an_expected_null_or_empty_check():
    check = module('protocol').score_check
    for path, value in [('narrative', None), ('anchors.*.id', [])]:
        result = check({}, {'path': path, 'op': 'equal', 'value': value, 'metric': 'safety'})
        assert result['passed'] is False
        assert result['actual'] == {'missing': True}


def test_protocol_survives_real_restarts_and_audits_selected_transitions():
    protocol = module('protocol')
    case = protocol.load_corpus()['cases'][0]
    report = protocol.run_protocol([case])
    assert report['passed']
    assert report['episodes'] == 1
    assert report['results'][0]['connection_lifetimes'] >= 3
    assert report['metrics']['audit_coverage']['failed'] == 0


@pytest.mark.parametrize('fault,family,metric', [
    ('empty', 'anchor_noise', 'retention'),
    ('forget', 'lexical_restart', 'stale_exposure'),
    ('pin', 'known_source', 'guard'),
    ('audit', 'canon_rotation', 'audit_coverage'),
])
def test_protocol_detects_broken_retention_withdrawal_guards_and_audits(monkeypatch, fault, family, metric):
    protocol = module('protocol')
    case = next(c for c in protocol.load_corpus()['cases'] if c['family'] == family)
    if fault == 'empty':
        monkeypatch.setattr(Store, 'recall', lambda *_a, **_k: {})
    elif fault == 'forget':
        monkeypatch.setattr(Store, 'forget', lambda self, memory_id, **kw: self.get(memory_id))
    elif fault == 'pin':
        monkeypatch.setattr(Store, 'pin', lambda *_a, **_k: {})
    else:
        monkeypatch.setattr(Store, '_log', lambda *_a, **_k: None)
    report = protocol.run_protocol([case])
    assert report['passed'] is False
    assert report['metrics'][metric]['failed'] > 0


def test_retrieval_arithmetic_penalizes_partial_and_missing_evidence():
    score = module('retrieval').score_retrieval
    assert score(['x', 'b', 'a'], ['a', 'b']) == {'recall': 1, 'hit': 1, 'mrr': .5}
    assert score(['x', 'a'], ['a', 'b']) == {'recall': .5, 'hit': 1, 'mrr': .5}
    assert score([], ['a']) == {'recall': 0, 'hit': 0, 'mrr': 0}
    with pytest.raises(ValueError):
        score(['a'], [])


def test_shared_budget_counts_utf8_bytes_and_preserves_whole_items():
    select = module('retrieval').apply_budget
    docs = [{'id': 'large', 'text': '中' * 3}, {'id': 'b', 'text': 'ab'}, {'id': 'c', 'text': '中'}]
    assert [r['id'] for r in select(docs, 2, 5)] == ['b', 'c']
    assert select(docs, 1, 1) == []
    with pytest.raises(ValueError):
        select(docs, 0, 5)


def test_reference_bm25_rewards_evidence_instead_of_recency():
    rank = module('retrieval').reference_bm25
    docs = [{'id': 'old', 'text': 'alpha evidence'}, {'id': 'new', 'text': 'unrelated cafe'}]
    assert [d['id'] for d in rank(docs, 'alpha')] == ['old', 'new']
    assert [d['id'] for d in rank(docs, 'the and')] == ['new', 'old']


def toy_data():
    return [{'sample_id': 'fixture', 'conversation': {
        'session_1_date_time': 'fixture date',
        'session_1': [{'dia_id': 'D1:1', 'speaker': 'A', 'text': 'alpha evidence'},
                      {'dia_id': 'D1:2', 'speaker': 'B', 'text': 'unrelated cafe'}]},
        'qa': [{'question': 'alpha', 'answer': 'NEVER_INDEX_THIS_ANSWER', 'evidence': ['D1:1'], 'category': 1},
               {'question': 'unknown', 'evidence': [], 'category': 4},
               {'question': 'adversarial', 'evidence': ['D1:1'], 'category': 5},
               {'question': 'broken annotation', 'evidence': ['D9:99'], 'category': 1}]}]


def test_external_adapter_keeps_labels_out_of_index_and_accounts_for_exclusions():
    prepared = module('retrieval').prepare_locomo(toy_data())
    assert 'NEVER_INDEX_THIS_ANSWER' not in json.dumps(prepared)
    assert set(prepared[0]['documents'][0]) == {'id', 'text'}
    assert [q['excluded'] for q in prepared[0]['queries']] == [None, 'no_evidence', 'adversarial', 'unresolved_evidence']


def test_external_read_rejects_changed_bytes_and_malformed_schema(tmp_path):
    retrieval = module('retrieval')
    path = tmp_path / 'data.json'
    path.write_text(json.dumps(toy_data()))
    with pytest.raises(ValueError, match='SHA-256'):
        retrieval.load_locomo(path, '0' * 64)
    assert retrieval.load_locomo(path, hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        retrieval.prepare_locomo([{'sample_id': 'bad', 'conversation': {}, 'qa': []}])


def test_external_comparison_has_equal_budget_and_no_fake_abstention_credit():
    report = module('retrieval').run_retrieval(toy_data(), max_items=1, max_bytes=4096)
    assert report['total_questions'] == 4
    assert report['scored_questions'] == 1
    assert sum(report['exclusions'].values()) == 3
    assert report['systems']['memory_as_history']['recall'] == 1
    assert report['systems']['reference_bm25']['recall'] == 1
    assert report['systems']['recency']['recall'] == 0
    for row in report['results']:
        assert row['content_bytes'] <= 4096
        assert len(row['selected_ids']) <= 1


def test_cli_fails_for_broken_protocol_report_and_writes_json(monkeypatch, tmp_path):
    cli = module('__main__')
    monkeypatch.setattr(cli, 'run_protocol', lambda *_a, **_k: {'passed': False, 'reason': 'negative control'})
    path = tmp_path / 'report.json'
    assert cli.main(['protocol', '--output', str(path)]) == 1
    assert json.loads(path.read_text())['passed'] is False
