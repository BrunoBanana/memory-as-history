"""Chronology and linked retrieval contracts, exercised through public Store APIs."""
import sqlite3
import threading

import pytest

from memory_as_history.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'history.db')
    yield value
    value.close()


def test_context_normalizes_event_time_and_survives_restart(tmp_path):
    path = tmp_path / 'persistent.db'
    s = Store(path)
    m = s.remember('Recorded later', event_at='2020-01-02T09:30:00+08:00', session_id='s', session_position=0)
    assert m.event_at == '2020-01-02T01:30:00+00:00'
    assert m.created_at != m.event_at
    s.close()
    s = Store(path)
    try:
        assert s.get(m.id).to_dict()['session_position'] == 0
        assert s.timeline()['memories'][0]['event_at'] == m.event_at
    finally:
        s.close()


@pytest.mark.parametrize('context', [
    {'event_at': '2025-01-01'}, {'event_at': '2025-01-01T01:00:00'},
    {'event_at': 'yesterday'}, {'event_at': 123}, {'session_id': ' '},
    {'session_position': 0}, {'session_id': 's', 'session_position': -1},
    {'session_id': 's', 'session_position': True},
])
def test_invalid_context_cannot_insert_memory(store, context):
    with pytest.raises(ValueError):
        store.remember('invalid', **context)
    assert store.recall()['memories'] == []


def test_context_replacement_is_audited_and_rollback_safe(store, monkeypatch):
    m = store.remember('record', session_id='s', session_position=0)
    changed = store.set_history_context(m.id, 'time corrected', event_at='2024-01-01T00:00:00Z')
    assert changed.session_id is None and changed.session_position is None
    log = store.audit_log()[0]
    assert log['action'] == 'set_history_context' and 'time corrected' in log['reason']
    assert 'before' in log['reason'] and 'after' in log['reason']
    def fail(*_):
        raise sqlite3.OperationalError('audit fault')
    monkeypatch.setattr(store, '_log', fail)
    with pytest.raises(sqlite3.OperationalError):
        store.set_history_context(m.id, 'clear time')
    assert store.get(m.id).event_at == changed.event_at


def test_session_positions_are_unambiguous_even_after_forgetting(store):
    m = store.remember('first', session_id='s', session_position=0)
    store.forget(m.id, 'withdraw')
    with pytest.raises(ValueError, match='position'):
        store.remember('duplicate', session_id='s', session_position=0)
    n = store.remember('other', session_id='t', session_position=0)
    with pytest.raises(ValueError, match='position'):
        store.set_history_context(n.id, 'move', session_id='s', session_position=0)
    assert store.get(n.id).session_id == 't'


def test_timeline_order_filters_unknowns_and_strict_limit(store):
    later = store.remember('later', event_at='2024-02-01T00:00:00Z', frame='a')
    unknown = store.remember('unknown', frame='a')
    earlier = store.remember('earlier', event_at='2024-01-01T00:00:00Z', frame='a')
    hidden = store.remember('hidden', event_at='2024-01-02T00:00:00Z', frame='a')
    store.forget(hidden.id, 'withdraw')
    store.remember('other frame', event_at='2024-01-03T00:00:00Z', frame='b')
    rows = store.timeline(frame='a')
    assert [r['id'] for r in rows['memories']] == [earlier.id, later.id, unknown.id]
    assert rows['unknown_event_times'] == 1
    assert [r['id'] for r in store.timeline(frame='a', since='2024-01-01T00:00:00Z', until='2024-01-01T00:00:00Z')['memories']] == [earlier.id]
    assert store.timeline(limit=0)['memories'] == []
    with pytest.raises(ValueError):
        store.timeline(since='2025-01-01T00:00:00Z', until='2024-01-01T00:00:00Z')


def test_directed_links_retraction_relink_and_audit(store):
    a, b = store.remember('new'), store.remember('prior')
    edge = store.link_memories(a.id, b.id, 'updates', 'notice revises prior')
    assert store.link_memories(a.id, b.id, 'updates', 'repeat')['id'] == edge['id']
    assert store.memory_links(a.id)[0]['to_id'] == b.id
    assert store.memory_links(b.id)[0]['from_id'] == a.id
    retired = store.unlink_memories(edge['id'], 'link was mistaken')
    assert retired['retired_at'] and retired['retirement_reason'] == 'link was mistaken'
    assert store.memory_links(a.id) == []
    assert len(store.memory_links(a.id, include_retired=True)) == 1
    fresh = store.link_memories(a.id, b.id, 'updates', 'reverified relationship')
    assert fresh['id'] != edge['id']
    assert store.get(b.id).status == 'working'
    assert store.provenance(b.id)['corroboration_satisfied'] is False
    assert {r['action'] for r in store.audit_log()} >= {'link_memories', 'unlink_memories'}


def test_links_reject_missing_forgotten_self_invalid_and_blank(store):
    a, b = store.remember('a'), store.remember('b')
    for args in [(a.id,a.id,'related','reason'), (a.id,b.id,'causes','reason'), (a.id,b.id,'related',' ')]:
        with pytest.raises(ValueError):
            store.link_memories(*args)
    with pytest.raises(KeyError):
        store.link_memories(a.id, 'missing', 'related', 'reason')
    store.forget(b.id, 'withdraw')
    with pytest.raises(ValueError):
        store.link_memories(a.id, b.id, 'related', 'reason')
    assert store.memory_links(a.id) == []


def test_link_and_unlink_audit_faults_roll_back(store, monkeypatch):
    a,b = store.remember('a'),store.remember('b')
    original = store._log
    def fail(*_):
        raise sqlite3.OperationalError('audit fault')
    monkeypatch.setattr(store, '_log', fail)
    with pytest.raises(sqlite3.OperationalError):
        store.link_memories(a.id,b.id,'related','reason')
    assert store.memory_links(a.id, include_retired=True) == []
    monkeypatch.setattr(store,'_log',original)
    edge=store.link_memories(a.id,b.id,'related','reason')
    monkeypatch.setattr(store,'_log',fail)
    with pytest.raises(sqlite3.OperationalError):
        store.unlink_memories(edge['id'],'withdraw')
    assert store.memory_links(a.id)[0]['retired_at'] is None


def test_legacy_schema_adds_context_without_inventing_time(tmp_path):
    from memory_as_history.storage import SCHEMA
    path=tmp_path/'legacy.db'
    con=sqlite3.connect(path)
    con.executescript(SCHEMA)
    for column in ('event_at','session_id','session_position'):
        if column in {r[1] for r in con.execute('PRAGMA table_info(memories)')}:
            con.execute(f'ALTER TABLE memories DROP COLUMN {column}')
    con.execute("INSERT INTO memories(id,content,created_at) VALUES ('legacy','unchanged','2020-01-01')")
    con.commit();con.close()
    s=Store(path)
    try:
        m=s.get('legacy')
        assert m.content == 'unchanged' and m.event_at is None and m.session_id is None
        assert s.audit_log() == []
    finally:
        s.close()


def history_fixture(store):
    seed = store.remember('launch deadline launch deadline', session_id='s', session_position=0, frame='a')
    neighbor = store.remember('Missing signature on the checklist', session_id='s', session_position=1, frame='a')
    prior = store.remember('R1: March 4', session_id='old', session_position=0, frame='a')
    noise = [store.remember(f'launch deadline planning {i}', frame='a') for i in range(5)]
    edge = store.link_memories(seed.id, prior.id, 'updates', 'revises original notice')
    return seed, neighbor, prior, noise, edge


def test_history_expansion_preserves_three_seeds_and_explains_paths(store):
    seed, neighbor, prior, noise, edge = history_fixture(store)
    plain = store.search_history('launch deadline', mode='lexical', expand='none', limit=5, frame='a')
    base_ids = [r['id'] for r in plain['memories']]
    assert base_ids == [r['id'] for r in store.recall('launch deadline', limit=5, frame='a')['memories']]
    result = store.search_history('launch deadline', mode='lexical', limit=5, frame='a')
    ids = [r['id'] for r in result['memories']]
    assert ids[:3] == base_ids[:3] and prior.id in ids and len(ids) == 5
    path = next(p for p in result['retrieval']['evidence_paths'] if p['memory_id'] == prior.id)
    assert path['seed_id'] == seed.id and path['link_id'] == edge['id'] and path['relation'] == 'updates'
    session = store.search_history('launch deadline', mode='lexical', expand='session', limit=5, frame='a')
    assert neighbor.id in [r['id'] for r in session['memories']]


def test_history_search_filters_time_without_overriding_anchor_or_canon(store):
    a = store.remember('global anchor', frame='elsewhere')
    store.promote(a.id, 'identity'); store.pin(a.id, 'identity')
    seed = store.remember('deadline', event_at='2024-01-10T00:00:00Z', frame='a')
    other = store.remember('deadline deadline', event_at='2025-01-10T00:00:00Z', frame='a')
    unknown = store.remember('deadline', frame='a')
    store.link_memories(seed.id, other.id, 'updates', 'related but outside time window')
    result = store.search_history('deadline', frame='a', mode='lexical', since='2024-01-01T00:00:00Z', until='2024-12-31T23:59:59Z', limit=5)
    assert [r['id'] for r in result['anchors']] == [a.id]
    assert [r['id'] for r in result['memories']] == [seed.id]
    assert result['retrieval']['time_excluded_candidates'] == 2
    assert store.search_history('deadline', mode='lexical', limit=0)['memories'] == []
    assert len(store.search_history('deadline', mode='lexical', limit=0)['anchors']) == 1


@pytest.mark.parametrize('change', ['forget', 'frame', 'unlink', 'context'])
def test_history_final_snapshot_rechecks_mutations_during_encoder(store, change):
    seed,neighbor,prior,noise,edge = history_fixture(store)
    class MutatingBackend:
        def similarities(self, query, texts):
            if change == 'forget': store.forget(prior.id, 'withdraw')
            elif change == 'frame': store.set_frame(prior.id, 'other', 'reframe')
            elif change == 'unlink': store.unlink_memories(edge['id'], 'withdraw link')
            else: store.set_history_context(prior.id, 'time corrected', event_at='2030-01-01T00:00:00Z')
            return [1. if 'launch' in t else 0. for t in texts]
        def describe(self): return {'model': 'controlled'}
    store._semantic_backend = MutatingBackend()
    if change == 'context':
        for m in [seed,neighbor,prior,*noise]:
            store.set_history_context(m.id, 'fixture time', event_at='2024-01-01T00:00:00Z')
    result = store.search_history('launch deadline', mode='semantic', expand='links', limit=5, frame='a',
                                  until='2025-01-01T00:00:00Z' if change=='context' else None)
    assert prior.id not in [r['id'] for r in result['memories']]
    assert all(prior.id not in (p['memory_id'],p.get('seed_id')) for p in result['retrieval']['evidence_paths'])


def test_history_encoding_does_not_hold_store_or_database_lock(store):
    seed, *_ = history_fixture(store)
    errors=[]
    class Backend:
        def similarities(self, query, texts):
            def writer():
                try:
                    second=Store(store.db_path)
                    try: second.forget(seed.id, 'concurrent withdrawal')
                    finally: second.close()
                except Exception as exc: errors.append(exc)
            thread=threading.Thread(target=writer);thread.start();thread.join(3)
            assert not thread.is_alive(), 'encoding held database writer lock'
            return [0.]*len(texts)
        def describe(self): return {}
    store._semantic_backend=Backend()
    result=store.search_history('launch',mode='hybrid',frame='a')
    assert not errors and seed.id not in [r['id'] for r in result['memories']]


def test_expansion_cannot_bridge_frames_forgetting_or_upgrade_authority(store):
    seed,neighbor,prior,noise,edge=history_fixture(store)
    store.set_frame(prior.id,'private','frame changed')
    store.forget(neighbor.id,'withdraw')
    result=store.search_history('launch',mode='lexical',frame='a',limit=20)
    ids=[r['id'] for r in result['memories']]
    assert prior.id not in ids and neighbor.id not in ids
    assert result['retrieval']['evidence_paths'] == []
    store.flag_sensitive(seed.id,'unverified identity claim')
    store.promote(seed.id,'keep history')
    store.search_history('launch',mode='lexical',frame='a')
    with pytest.raises(PermissionError): store.pin(seed.id,'link is not corroboration')


@pytest.mark.parametrize('options', [{'mode':'unknown'}, {'expand':'recursive'}, {'limit':True}, {'since':'tomorrow'}])
def test_invalid_history_search_options(store,options):
    with pytest.raises(ValueError): store.search_history('query',**options)


def test_single_seed_can_supply_two_distinct_evidence_links_within_budget(store):
    seed,neighbor,prior,noise,edge=history_fixture(store)
    store.link_memories(neighbor.id,seed.id,'explains','checklist explains the change')
    result=store.search_history('launch deadline',mode='lexical',expand='links',limit=5)
    assert {seed.id,neighbor.id,prior.id} <= {r['id'] for r in result['memories']}
    assert len(result['memories']) == 5
    assert {p['seed_id'] for p in result['retrieval']['evidence_paths']} == {seed.id}


def test_changed_event_context_requires_dependent_narrative_review(store):
    m=store.remember('deadline changed', event_at='2025-01-01T00:00:00Z')
    store.narrate('January change', 'timeline synthesis',[m.id])
    store.set_history_context(m.id, 'date was misrecorded', event_at='2025-02-01T00:00:00Z')
    assert store.recall()['narrative'] is None
    assert store.current_narrative()['review_status'] == 'stale'
