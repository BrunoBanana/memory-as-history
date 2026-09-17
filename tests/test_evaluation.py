"""The evaluation must detect regressions and fail its command, not just print."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def evaluation():
    path = Path(__file__).resolve().parents[1] / 'usefulness_test.py'
    spec = importlib.util.spec_from_file_location('usefulness_evaluation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_corpus_and_retention_contract(evaluation):
    report = evaluation.evaluate()
    assert report['passed'] is True
    assert report['failures'] == []
    assert len(report['anchor_retention']['cases']) == 4
    assert report['anchor_retention']['pass_rate'] == 1
    assert report['lexical_retrieval']['corpus_size'] == 20
    assert report['lexical_retrieval']['query_count'] == 12
    assert report['lexical_retrieval']['hit_at_1'] == 1
    assert report['lexical_retrieval']['mrr'] == 1


def test_lost_anchor_fails_machine_readable_command(evaluation, monkeypatch, capsys):
    monkeypatch.setattr(evaluation, 'run_memory_as_history', lambda *_: (False, {}))
    assert evaluation.main(['--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['passed'] is False
    assert len(report['failures']) == 4
    assert report['anchor_retention']['pass_rate'] == 0


def test_broken_ranking_is_detected_by_fixture(evaluation, monkeypatch):
    monkeypatch.setattr(evaluation.Store, '_rank_by_query', lambda self, rows, query: rows)
    report = evaluation.evaluate()
    assert report['passed'] is False
    assert report['lexical_retrieval']['hit_at_1'] < 1
    assert report['lexical_retrieval']['mrr'] < 1
    assert report['failures']
