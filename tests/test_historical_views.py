"""Independent narrative perspectives and discoverable archival counterevidence."""
import json
import sqlite3

import pytest

from memory_as_history.storage import Store
from test_knowledge import supported, snapshot
from test_transactions import race_connections


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / 'views.db')
    yield instance
    instance.close()


def test_scope_versions_do_not_silently_replace_other_perspectives(store):
    memory = store.remember('Released on schedule; an outage followed')
    global_account = store.narrate('Global context', 'initial')
    product = store.narrate('On-time release', 'schedule criterion', [memory.id],
                            scope='launch/product', perspective='Product team; schedule',
                            coverage='Published release note only')
    operations = store.narrate('Reliability incident', 'availability criterion', [memory.id],
                               scope='launch/operations', perspective='Operations; availability')
    assert store.current_narrative()['id'] == global_account['id']
    assert store.recall()['narrative']['id'] == global_account['id']
    assert store.current_narrative(scope='launch/product')['id'] == product['id']
    update = store.narrate('Met the planned date', 'wording clarified', [memory.id], scope='launch/product')
    assert store.current_narrative(scope='launch/operations')['id'] == operations['id']
    history = store.narrative_history(scope='launch/product')
    assert {n['id'] for n in history} == {product['id'], update['id']}
    assert next(n for n in history if n['id'] == product['id'])['superseded_by'] == update['id']
    assert {n['scope'] for n in store.list_narratives()} == {'global','launch/product','launch/operations'}


def test_relation_retirement_invalidates_only_explicitly_dependent_narrative(store):
    first = store.remember('Migration began')
    second = store.remember('The outage began')
    link = store.link_memories(first.id, second.id, 'explains', 'working causal interpretation')
    dependent = store.narrate('Migration may explain outage', 'hypothesis', scope='cause', link_ids=[link['id']])
    unrelated = store.narrate('Outage was recorded', 'record', [second.id], scope='outage')
    assert set(dependent['memory_ids']) == {first.id, second.id}
    store.unlink_memories(link['id'], 'investigation did not support the explanation')
    current = store.current_narrative(scope='cause')
    assert current['review_status'] == 'stale' and current['review_required_at']
    assert any(i.get('link_id') == link['id'] for i in current['source_issues'])
    assert store.current_narrative(scope='outage')['review_status'] == 'current'
    with pytest.raises(ValueError, match='source issues'):
        store.review_narrative(dependent['id'], 'cannot simply approve a retired explanation')
    assert unrelated['id'] != dependent['id']


def test_claim_revision_invalidates_dependent_synthesis_and_retains_versions(store):
    _, old, _ = supported(store)
    store.adopt_claim(old['id'], 'initial')
    dependent = store.narrate('June plan', 'initial synthesis', claim_ids=[old['id']])
    assert dependent['memory_ids']
    _, replacement, _ = supported(store, 'July plan')
    store.revise_claim(old['id'], replacement['id'], 'notice changed')
    assert store.recall()['narrative'] is None
    assert store.current_narrative()['review_required_at']
    new = store.narrate('July plan', 'updated synthesis', claim_ids=[replacement['id']])
    assert store.current_narrative()['id'] == new['id']
    assert store.narrative_history()[1]['content'] == 'June plan'


def test_new_evidence_requires_claim_readoption_and_narrative_review(store):
    _, claim, _ = supported(store)
    store.adopt_claim(claim['id'], 'adopt')
    narrative = store.narrate('A forecast', 'synthesis', claim_ids=[claim['id']])
    counter = store.remember('Operations questions the forecast')
    store.add_evidence(claim['id'], counter.id, 'challenges', 'new counterevidence')
    assert store.recall()['narrative'] is None
    store.adopt_claim(claim['id'], 'still an explicitly disputed forecast')
    assert store.recall()['narrative'] is None
    store.review_narrative(narrative['id'], 'summary checked against the new dispute')
    assert store.recall()['narrative']['id'] == narrative['id']
    store.forget(counter.id, 'withdraw access to the counterevidence')
    assert store.recall()['narrative'] is None


def test_forgetting_relation_endpoint_invalidates_account(store):
    first = store.remember('Statement one')
    second = store.remember('Statement two')
    link = store.link_memories(first.id, second.id, 'related', 'same discussion')
    store.narrate('Combined account', 'synthesis', link_ids=[link['id']])
    store.forget(first.id, 'withdrawn')
    assert store.recall()['narrative'] is None


@pytest.mark.parametrize('kwargs', [
    {'scope':' '}, {'perspective':' '}, {'coverage':''}, {'claim_ids':['missing']},
    {'link_ids':['missing']}, {'claim_ids':'not-a-list'}, {'link_ids':[1]},
])
def test_invalid_scoped_dependencies_cannot_supersede_existing(store, kwargs):
    old = store.narrate('Current account', 'initial')
    before = snapshot(store)
    with pytest.raises((ValueError, KeyError)):
        store.narrate('Bad replacement', 'new', **kwargs)
    assert snapshot(store) == before
    assert store.current_narrative()['id'] == old['id']


def test_unadopted_claim_is_not_a_valid_current_narrative_dependency(store):
    _, claim, _ = supported(store)
    with pytest.raises(ValueError, match='source issues'):
        store.narrate('Unadopted claim treated as current', 'invalid', claim_ids=[claim['id']])


def test_sensitive_claim_cannot_be_laundered_through_narrative(store):
    memory, claim, _ = supported(store, security_sensitive=True)
    store.corroborate(memory.id, 'independent register')
    store.adopt_claim(claim['id'], 'verified source labels')
    ordinary = store.remember('Additional unverified material')
    with pytest.raises(PermissionError, match='corroboration'):
        store.narrate('Combined account', 'synthesis', [ordinary.id], claim_ids=[claim['id']])


def test_archive_budget_finds_counterevidence_when_anchors_fill_context(store):
    for index in range(4):
        anchor = store.remember(f'Current identity label {index}', frame='identity')
        store.promote(anchor.id, 'durable')
        store.pin(anchor.id, 'important')
    counter = store.remember('Release outage invalidates success assessment', frame='operations',
                              material_type='document', capture_context='Incident log')
    assert store.recall(query='release outage', limit=1)['memories'] == []
    found = store.search_archive('release outage', limit=1)
    assert [m['id'] for m in found['memories']] == [counter.id]
    assert found['retrieval']['eligible_count'] == 5
    assert found['retrieval']['truncated']
    assert found['retrieval']['priority_policy'] == 'no_reserved_slots'
    assert found['memories'][0]['capture_context'] == 'Incident log'
    assert found['retrieval']['collection_completeness'] == 'unknown'


def test_archive_filters_apply_to_anchors_too_and_preserve_unknown_dates(store):
    anchor = store.remember('Release fact', frame='other', event_at='2024-01-01T00:00:00Z')
    store.promote(anchor.id, 'important'); store.pin(anchor.id, 'anchor')
    undated = store.remember('Release undated', frame='team', session_id='one')
    dated = store.remember('Release observation', frame='team', session_id='one', event_at='2024-01-02T00:00:00Z')
    view = store.search_archive('Release', frame='team', session_id='one')
    assert {m['id'] for m in view['memories']} == {undated.id, dated.id}
    assert view['retrieval']['unknown_event_times'] == 1
    bounded = store.search_archive('Release', frame='team', since='2024-01-01T00:00:00Z')
    assert [m['id'] for m in bounded['memories']] == [dated.id]
    assert bounded['retrieval']['unknown_event_times'] == 1
    store.forget(dated.id, 'withdrawn')
    assert store.search_archive('Release', since='2024-01-01T00:00:00Z', frame='team')['memories'] == []


def test_archive_does_not_change_priorities_or_classify_material_as_true(store):
    store.remember('Interview statement', material_type='utterance')
    before = snapshot(store)
    result = store.search_archive('statement', limit=0)
    assert result['memories'] == [] and result['retrieval']['eligible_count'] == 1
    assert snapshot(store) == before
    assert store.search_archive('statement')['memories'][0]['tier'] == 'archive'


@pytest.mark.parametrize('kwargs', [{'limit':True}, {'limit':-1}, {'since':'yesterday'}, {'query':' '}])
def test_archive_rejects_invalid_filters(store, kwargs):
    with pytest.raises(ValueError):
        store.search_archive(**{'query':'release', **kwargs})


def test_scoped_narrative_race_retains_both_current_accounts(store):
    other = Store(store.db_path)
    try:
        results = race_connections(store, other,
            lambda: store.narrate('Product view', 'schedule', scope='product'),
            lambda: other.narrate('Operations view', 'availability', scope='operations'),
            'INSERT INTO narratives')
        assert all(not isinstance(value, Exception) for value in results.values())
        assert len(store.list_narratives()) == 2
    finally:
        other.close()


def test_failed_dependency_invalidation_rolls_back_relationship_retirement(store):
    a, b = store.remember('one'), store.remember('two')
    link = store.link_memories(a.id, b.id, 'explains', 'hypothesis')
    store.narrate('Explanation', 'synthesis', link_ids=[link['id']])
    before = snapshot(store)
    store._conn.executescript("""CREATE TRIGGER fail_invalidation BEFORE INSERT ON audit_log
        WHEN NEW.action='narrative_invalidated' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match='audit unavailable'):
        store.unlink_memories(link['id'], 'retracted')
    assert snapshot(store) == before


def test_claim_and_narrative_invalidation_failure_rolls_back_forgetting(store):
    memory, claim, _ = supported(store)
    store.adopt_claim(claim['id'], 'adopt')
    store.narrate('Summary', 'synthesis', claim_ids=[claim['id']])
    before = snapshot(store)
    store._conn.executescript("""CREATE TRIGGER fail_invalidation BEFORE INSERT ON audit_log
        WHEN NEW.action='claim_source_changed' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match='audit unavailable'):
        store.forget(memory.id, 'withdraw')
    assert snapshot(store) == before


@pytest.mark.parametrize('field', ['claim_ids', 'link_ids'])
def test_corrupt_narrative_dependencies_fail_closed(store, field):
    narrative = store.narrate('Legacy summary', 'initial')
    store._conn.execute(f'UPDATE narratives SET {field}=? WHERE id=?', ('[42]', narrative['id']))
    store._conn.commit()
    assert store.recall()['narrative'] is None
    assert store.current_narrative()['source_issues'][0]['issue'] == 'invalid_' + field


def test_scope_discovery_withholds_stale_text_but_legacy_inspection_retains_it(store):
    memory = store.remember('Withdrawn private wording')
    narrative = store.narrate('Withdrawn private wording', 'Withdrawn private wording', [memory.id],
                              scope='profile', perspective='Withdrawn private wording',
                              coverage='Withdrawn private wording')
    store.forget(memory.id, 'withdrawn')
    listed = store.list_narratives()
    assert listed[0]['id'] == narrative['id'] and listed[0]['content'] is None
    assert 'Withdrawn private wording' not in json.dumps(listed)
    assert store.current_narrative(scope='profile')['content'] == 'Withdrawn private wording'
