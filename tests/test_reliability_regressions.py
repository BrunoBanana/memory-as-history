"""Cross-module regressions missing from the original storage baseline."""

import pytest

from memory_as_history.storage import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "regressions.db")
    yield instance
    instance.close()


@pytest.mark.parametrize("source", ["", "  ", "\t\n"])
def test_blank_origin_requires_two_distinct_corroborating_sources(store, source):
    memory = store.remember("An identity claim", source=source, security_sensitive=True)
    store.promote(memory.id, reason="identity")
    store.corroborate(memory.id, source="voice-one")
    assert store.independent_corroboration_count(memory.id) == 0
    with pytest.raises(PermissionError):
        store.pin(memory.id, reason="one voice cannot establish an unknown origin")
    store.corroborate(memory.id, source=" voice-one ")
    assert store.independent_corroboration_count(memory.id) == 0
    store.corroborate(memory.id, source="voice-two")
    store.pin(memory.id, reason="two independent voices")
    assert store.is_anchored(memory.id)


def test_forgotten_memory_cannot_acquire_hidden_anchor(store):
    memory = store.remember("A former identity fact")
    store.promote(memory.id, reason="identity")
    store.forget(memory.id, reason="no longer applies")
    before = store.audit_log()
    with pytest.raises(ValueError, match="forgotten"):
        store.pin(memory.id, reason="must restore before pinning")
    assert not store.is_anchored(memory.id)
    assert store.audit_log() == before
    store.restore(memory.id, reason="fact applies again")
    assert store.recall()["anchors"] == []
    store.pin(memory.id, reason="explicitly re-established")
    assert store.is_anchored(memory.id)


def test_recall_deduplicates_multiscope_canon_before_spending_budget(store):
    memory = store.remember("A decision shared by two projects")
    store.promote(memory.id, reason="durable decision")
    store.canonize(memory.id, scope="project-one", reason="active")
    store.canonize(memory.id, scope="project-two", reason="also active")
    other = store.remember("Another useful fact")

    result = store.recall(limit=2)
    assert [entry["memory_id"] for entry in result["canon"]] == [memory.id]
    assert [entry["id"] for entry in result["memories"]] == [other.id]
    assert len(store.list_canon()) == 2  # Both task memberships remain intact.
    store.end_scope("project-one", reason="finished")
    assert store.recall(limit=2)["canon"][0]["scope"] == "project-two"


@pytest.mark.parametrize("forgotten_side", ["a", "b"])
def test_recall_hides_conflicts_with_forgotten_memories_but_keeps_history(
    store, forgotten_side,
):
    a = store.remember("The launch is Monday", frame="team-one")
    b = store.remember("The launch is Friday", frame="team-two")
    conflict = store.mark_conflict(a.id, b.id, reason="conflicting reports")
    forgotten = a if forgotten_side == "a" else b
    store.forget(forgotten.id, reason="report withdrawn")

    assert store.recall()["conflicts"] == []
    history = store.list_conflicts(resolved=False)
    assert [row["id"] for row in history] == [conflict["id"]]
    assert store.get(forgotten.id).is_forgotten
    store.restore(forgotten.id, reason="report reinstated")
    assert [row["id"] for row in store.recall()["conflicts"]] == [conflict["id"]]


@pytest.mark.parametrize("anchored", [False, True])
def test_recall_reports_consistent_stale_status_on_first_read(store, anchored):
    store.interpretation_review_days = 0
    memory = store.remember("Inferred user preference", tier="interpretation")
    if anchored:
        store.promote(memory.id, reason="recurring preference")
        store.pin(memory.id, reason="core preference")

    result = store.recall()
    section = "anchors" if anchored else "memories"
    assert result[section][0]["review_status"] == "stale"
    assert result["stale_interpretations"][0]["review_status"] == "stale"
    assert store.get(memory.id).review_status == "stale"
