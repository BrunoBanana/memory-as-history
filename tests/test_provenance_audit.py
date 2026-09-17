"""Evidence gates and accountable anchor removal, including legacy data."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from memory_as_history.storage import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "provenance.db")
    yield instance
    instance.close()


def events(store, memory_id, action):
    return [row for row in store.audit_log()
            if row["memory_id"] == memory_id and row["action"] == action]


def anchor(store):
    memory = store.remember("A durable preference")
    store.promote(memory.id, "durable")
    store.pin(memory.id, "identity")
    return memory


def test_same_source_cannot_upgrade_or_authorize_sensitive_pin(store):
    memory = store.remember("A claimed fact", source=" original ", security_sensitive=True)
    store.promote(memory.id, "durable")
    for source in ["original", " original "]:
        assert store.corroborate(memory.id, source).tier == "archive"
        with pytest.raises(PermissionError):
            store.pin(memory.id, "identity")
    assert store.corroborate(memory.id, " independent ").tier == "testimony"
    store.pin(memory.id, "independently supported")
    assert store.is_anchored(memory.id)
    assert len(events(store, memory.id, "corroborate_upgrade")) == 1
    store.corroborate(memory.id, "independent")
    assert len(events(store, memory.id, "corroborate_upgrade")) == 1


@pytest.mark.parametrize("origin", [None, "", " \t "])
def test_unknown_origin_needs_two_distinct_sources_for_both_gates(store, origin):
    memory = store.remember("A claimed fact", source=origin, security_sensitive=True)
    store.promote(memory.id, "durable")
    for source in ["report-a", " report-a "]:
        assert store.corroborate(memory.id, source).tier == "archive"
        with pytest.raises(PermissionError):
            store.pin(memory.id, "identity")
    assert store.corroborate(memory.id, "report-b").tier == "testimony"
    store.pin(memory.id, "supported")
    assert store.is_anchored(memory.id)


def test_direct_testimony_capture_is_rejected_without_writes(store):
    with pytest.raises(ValueError, match="testimony.*corroborate"):
        store.remember("Unverified", source="one", tier="testimony")
    assert store.recall()["memories"] == []
    assert store.audit_log() == []


@pytest.mark.parametrize("tier", ["archive", "interpretation"])
def test_every_corroboration_is_audited_even_without_upgrade(store, tier):
    memory = store.remember("An account", source="origin", tier=tier)
    for source in ["origin", " origin ", "other", "other"]:
        store.corroborate(memory.id, source)
    logged = events(store, memory.id, "corroborate")
    assert len(logged) == 4
    assert all(row["reason"] and row["at"] for row in logged)
    assert sum("origin" in row["reason"] for row in logged) == 2
    if tier == "interpretation":
        assert store.get(memory.id).tier == "interpretation"
        assert not events(store, memory.id, "corroborate_upgrade")


def test_failed_corroboration_audit_rolls_back_non_upgrading_evidence(store):
    memory = store.remember("A fact", source="origin")
    store._conn.executescript("""
        CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log
        WHEN NEW.action = 'corroborate'
        BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        store.corroborate(memory.id, "origin")
    store.remember("Following successful write")
    assert store._conn.execute("SELECT COUNT(*) FROM corroborations").fetchone()[0] == 0
    assert store.get(memory.id).tier == "archive"


def test_provenance_normalizes_sources_and_is_read_only(store):
    memory = store.remember("A fact", source=" origin ")
    for source in ["origin", "other", " other "]:
        store.corroborate(memory.id, source)
    before = store._conn.total_changes
    support = store.provenance(memory.id)
    assert support == {
        "memory_id": memory.id, "tier": "testimony", "source": " origin ",
        "origin_known": True, "corroborating_sources": ["origin", "other"],
        "independent_corroboration_count": 1, "corroboration_satisfied": True,
    }
    assert store.independent_corroboration_count(memory.id) == 1
    assert store._conn.total_changes == before


def test_legacy_testimony_is_preserved_and_unsupported_evidence_is_visible(store):
    memory = store.remember("An old account")
    # Model a pre-1.2 row: direct testimony and blank historic corroboration.
    store._conn.execute("UPDATE memories SET tier='testimony' WHERE id=?", (memory.id,))
    store._conn.execute(
        "INSERT INTO corroborations (id, memory_id, source, at) VALUES ('old', ?, '  ', 'old')",
        (memory.id,),
    )
    store._conn.commit()
    reopened = Store(store.db_path)
    try:
        assert reopened.get(memory.id).tier == "testimony"
        support = reopened.provenance(memory.id)
        assert support["origin_known"] is False
        assert support["corroborating_sources"] == []
        assert support["independent_corroboration_count"] == 0
        assert support["corroboration_satisfied"] is False
        assert "warning" in support
        assert reopened.audit_log() == []
        reopened.corroborate(memory.id, "report-a")
        assert reopened.provenance(memory.id)["corroboration_satisfied"] is False
        reopened.corroborate(memory.id, "report-b")
        assert "warning" not in reopened.provenance(memory.id)
        assert not events(reopened, memory.id, "corroborate_upgrade")
    finally:
        reopened.close()


def test_provenance_unknown_memory_raises(store):
    with pytest.raises(KeyError):
        store.provenance("missing")


def test_unpin_records_reason_and_distinguishes_noop_without_changing_return(store):
    memory = anchor(store)
    assert store.unpin(memory.id, reason=" no longer defining ") is None
    assert not store.is_anchored(memory.id)
    assert events(store, memory.id, "unpin")[0]["reason"] == " no longer defining "
    assert store.unpin(memory.id, reason="already removed") is None
    assert len(events(store, memory.id, "unpin")) == 1
    assert events(store, memory.id, "unpin_noop")[0]["reason"] == "already removed"
    assert store.get(memory.id).content == memory.content


@pytest.mark.parametrize("reason", ["", " \t "])
def test_empty_explicit_unpin_reason_leaves_anchor_and_audit_unchanged(store, reason):
    memory = anchor(store)
    before = store.audit_log()
    with pytest.raises(ValueError, match="reason"):
        store.unpin(memory.id, reason=reason)
    assert store.is_anchored(memory.id)
    assert store.audit_log() == before


@pytest.mark.parametrize("explicit_none", [False, True])
def test_legacy_unpin_records_that_caller_supplied_no_reason(store, explicit_none):
    memory = anchor(store)
    args = {"reason": None} if explicit_none else {}
    assert store.unpin(memory.id, **args) is None
    assert events(store, memory.id, "unpin")[0]["reason"] == (
        "legacy unpin: caller did not provide a reason"
    )


def test_unknown_id_unpin_preserves_idempotence_and_audits_noop(store):
    assert store.unpin("missing", reason="cleanup") is None
    assert events(store, "missing", "unpin_noop")[0]["reason"] == "cleanup"


def test_unpin_audit_failure_restores_anchor_even_after_next_commit(store):
    memory = anchor(store)
    before = store.audit_log()
    store._conn.executescript("""
        CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log
        WHEN NEW.action = 'unpin'
        BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        store.unpin(memory.id, reason="retired")
    store.remember("Following successful write")
    reopened = Store(store.db_path)
    try:
        assert reopened.is_anchored(memory.id)
        assert reopened.audit_log() == before
    finally:
        reopened.close()


def test_concurrent_unpins_record_exactly_one_removal(store):
    memory = anchor(store)
    ready = Barrier(2)

    def remove():
        connection = Store(store.db_path)
        try:
            ready.wait(timeout=5)
            connection.unpin(memory.id, reason="retired")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(remove) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert len(events(store, memory.id, "unpin")) == 1
    assert len(events(store, memory.id, "unpin_noop")) == 1
