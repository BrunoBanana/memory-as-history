import tempfile
from pathlib import Path

import pytest

from memory_as_history.storage import Store


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = Store(
            Path(d) / "test.db",
            anchor_soft_limit=2,
            interpretation_review_days=30,
        )
        yield s
        s.close()


# -- consolidation (Assmann) -------------------------------------------------


def test_remember_creates_working_archive_memory(store: Store):
    mem = store.remember("user prefers dark mode", source="chat")
    assert mem.status == "working"
    assert mem.tier == "archive"
    assert mem.content == "user prefers dark mode"


def test_remember_rejects_empty_content(store: Store):
    with pytest.raises(ValueError):
        store.remember("   ")


def test_remember_rejects_invalid_tier(store: Store):
    with pytest.raises(ValueError):
        store.remember("x", tier="nonsense")


def test_promote_requires_reason_and_logs(store: Store):
    mem = store.remember("decided to use SQLite for v0.1")
    promoted = store.promote(mem.id, reason="architecture decision, referenced repeatedly")
    assert promoted.status == "consolidated"
    assert promoted.consolidation_reason.startswith("architecture decision")

    log = store.audit_log()
    assert any(e["action"] == "promote" and e["memory_id"] == mem.id for e in log)


def test_promote_rejects_empty_reason(store: Store):
    mem = store.remember("x")
    with pytest.raises(ValueError):
        store.promote(mem.id, reason="  ")


def test_promote_unknown_memory_raises(store: Store):
    with pytest.raises(KeyError):
        store.promote("does-not-exist", reason="n/a")


def test_promote_twice_updates_reason_without_resetting_status(store: Store):
    mem = store.remember("x")
    first = store.promote(mem.id, reason="first reason")
    second = store.promote(mem.id, reason="stronger reason later")
    assert first.consolidated_at == second.consolidated_at
    assert second.consolidation_reason == "stronger reason later"


# -- anchors (Nora) -----------------------------------------------------------


def test_pin_requires_consolidation_first(store: Store):
    mem = store.remember("not yet consolidated")
    with pytest.raises(ValueError):
        store.pin(mem.id, reason="too early")


def test_pin_creates_anchor_and_logs(store: Store):
    mem = store.remember("core identity: I am a memory-as-history agent")
    store.promote(mem.id, reason="foundational identity statement")
    result = store.pin(mem.id, reason="foundational identity statement")
    assert result["memory_id"] == mem.id
    assert "warning" not in result

    anchors = store.list_anchors()
    assert len(anchors) == 1
    assert anchors[0]["id"] == mem.id

    log = store.audit_log()
    assert any(e["action"] == "pin" for e in log)


def test_pin_rejects_empty_reason(store: Store):
    mem = store.remember("x")
    store.promote(mem.id, reason="r")
    with pytest.raises(ValueError):
        store.pin(mem.id, reason="")


def test_pin_warns_past_soft_limit(store: Store):
    # fixture uses anchor_soft_limit=2
    ids = []
    for i in range(3):
        mem = store.remember(f"anchor candidate {i}")
        store.promote(mem.id, reason="promoted for anchor test")
        ids.append(mem.id)

    store.pin(ids[0], reason="anchor 1")
    result2 = store.pin(ids[1], reason="anchor 2")
    result3 = store.pin(ids[2], reason="anchor 3")
    assert "warning" not in result2
    assert "warning" in result3


def test_unpin_removes_anchor_but_keeps_memory(store: Store):
    mem = store.remember("temporary anchor test")
    store.promote(mem.id, reason="r")
    store.pin(mem.id, reason="test")
    store.unpin(mem.id)
    assert store.list_anchors() == []
    assert store.get(mem.id) is not None


def test_recall_anchors_always_first_regardless_of_query(store: Store):
    anchor_mem = store.remember("anchor: identity is stable")
    store.promote(anchor_mem.id, reason="r")
    store.pin(anchor_mem.id, reason="identity anchor")
    store.remember("unrelated ordinary memory about weather")

    result = store.recall(query="weather", limit=5)
    anchor_ids = [a["id"] for a in result["anchors"]]
    assert anchor_mem.id in anchor_ids


def test_recall_prefers_consolidated_over_working(store: Store):
    working = store.remember("working memory item")
    consolidated = store.remember("consolidated memory item")
    store.promote(consolidated.id, reason="important")

    result = store.recall(limit=10)
    ids_in_order = [m["id"] for m in result["memories"]]
    assert ids_in_order.index(consolidated.id) < ids_in_order.index(working.id)


# -- provenance tiers (Ricoeur) ------------------------------------------------


def test_archive_upgrades_to_testimony_on_corroboration(store: Store):
    mem = store.remember("the game launched on 2026-01-15", tier="archive")
    corroborated = store.corroborate(mem.id, source="second independent report")
    assert corroborated.tier == "testimony"

    log = store.audit_log()
    assert any(e["action"] == "corroborate_upgrade" for e in log)


def test_corroborate_unknown_memory_raises(store: Store):
    with pytest.raises(KeyError):
        store.corroborate("nope", source="x")


def test_interpretation_memory_starts_current_and_is_not_upgraded_by_corroboration(
    store: Store,
):
    mem = store.remember("agent's inferred summary of user intent", tier="interpretation")
    assert mem.review_status == "current"
    assert mem.last_reviewed_at is not None

    store.corroborate(mem.id, source="unrelated observation")
    still = store.get(mem.id)
    assert still.tier == "interpretation"  # corroboration doesn't upgrade interpretation


def test_review_requires_interpretation_tier(store: Store):
    mem = store.remember("raw fact", tier="archive")
    with pytest.raises(ValueError):
        store.review(mem.id, note="trying to review a non-interpretation memory")


def test_review_resets_clock_and_logs(store: Store):
    mem = store.remember("agent's inferred summary", tier="interpretation")
    reviewed = store.review(mem.id, note="still holds after re-examination")
    assert reviewed.review_status == "current"

    log = store.audit_log()
    assert any(e["action"] == "review" and e["memory_id"] == mem.id for e in log)


def test_due_for_review_flags_old_interpretations(store: Store):
    mem = store.remember("agent's inferred summary", tier="interpretation")
    # force it to look overdue by asking with a 0-day threshold
    stale = store.due_for_review(days=0)
    assert any(r["id"] == mem.id for r in stale)
    assert store.get(mem.id).review_status == "stale"


def test_due_for_review_not_flagged_when_recent(store: Store):
    mem = store.remember("agent's inferred summary", tier="interpretation")
    stale = store.due_for_review(days=30)
    assert not any(r["id"] == mem.id for r in stale)


def test_recall_surfaces_stale_interpretations(store: Store):
    store.remember("agent's inferred summary", tier="interpretation")
    result = store.recall(limit=5)
    # with default interpretation_review_days=30 and a freshly created memory,
    # it should NOT be stale yet
    assert result["stale_interpretations"] == []


# -- forgetting (Ricoeur) -----------------------------------------------------


def test_forget_requires_reason(store: Store):
    mem = store.remember("temporary note")
    with pytest.raises(ValueError):
        store.forget(mem.id, reason="  ")


def test_forget_unknown_memory_raises(store: Store):
    with pytest.raises(KeyError):
        store.forget("nope", reason="x")


def test_forget_tombstones_without_deleting(store: Store):
    mem = store.remember("outdated preference")
    forgotten = store.forget(mem.id, reason="user explicitly said this no longer applies")
    assert forgotten.is_forgotten
    assert forgotten.forgotten_reason.startswith("user explicitly")
    # content is retained, not deleted
    assert store.get(mem.id) is not None
    assert store.get(mem.id).content == "outdated preference"

    log = store.audit_log()
    assert any(e["action"] == "forget" and e["memory_id"] == mem.id for e in log)


def test_forgotten_memory_excluded_from_recall(store: Store):
    mem = store.remember("to be forgotten, contains keyword zzzsearch")
    store.forget(mem.id, reason="no longer relevant")

    result = store.recall(query="zzzsearch", limit=10)
    ids = [m["id"] for m in result["memories"]]
    assert mem.id not in ids


def test_anchored_memory_cannot_be_forgotten_directly(store: Store):
    mem = store.remember("core identity fact")
    store.promote(mem.id, reason="r")
    store.pin(mem.id, reason="identity anchor")
    with pytest.raises(ValueError):
        store.forget(mem.id, reason="trying to forget an anchor")


def test_unpin_then_forget_works(store: Store):
    mem = store.remember("core identity fact")
    store.promote(mem.id, reason="r")
    store.pin(mem.id, reason="identity anchor")
    store.unpin(mem.id)
    forgotten = store.forget(mem.id, reason="identity revised, no longer an anchor")
    assert forgotten.is_forgotten


def test_forgotten_anchor_excluded_from_list_anchors_defensively(store: Store):
    # even if somehow forgotten while still anchored (shouldn't normally
    # happen given the guard above), list_anchors must not surface it
    mem = store.remember("edge case anchor")
    store.promote(mem.id, reason="r")
    store.pin(mem.id, reason="anchor")
    store.unpin(mem.id)
    store.forget(mem.id, reason="cleanup")
    assert store.list_anchors() == []


def test_forget_twice_updates_reason_without_resetting_timestamp(store: Store):
    mem = store.remember("x")
    first = store.forget(mem.id, reason="first reason")
    second = store.forget(mem.id, reason="better reason")
    assert first.forgotten_at == second.forgotten_at
    assert second.forgotten_reason == "better reason"


def test_restore_requires_reason(store: Store):
    mem = store.remember("x")
    store.forget(mem.id, reason="r")
    with pytest.raises(ValueError):
        store.restore(mem.id, reason="")


def test_restore_reverses_forgetting_and_logs(store: Store):
    mem = store.remember("to be restored, contains keyword yyysearch")
    store.forget(mem.id, reason="mistakenly thought obsolete")
    restored = store.restore(mem.id, reason="turned out still relevant")
    assert not restored.is_forgotten
    assert restored.forgotten_at is None

    result = store.recall(query="yyysearch", limit=10)
    ids = [m["id"] for m in result["memories"]]
    assert mem.id in ids

    log = store.audit_log()
    assert any(e["action"] == "restore" and e["memory_id"] == mem.id for e in log)


def test_restore_non_forgotten_memory_raises(store: Store):
    mem = store.remember("never forgotten")
    with pytest.raises(ValueError):
        store.restore(mem.id, reason="x")


def test_list_forgotten_returns_tombstones(store: Store):
    mem = store.remember("gone")
    store.forget(mem.id, reason="cleanup")
    tombstones = store.list_forgotten()
    assert any(t["id"] == mem.id for t in tombstones)


def test_due_for_review_excludes_forgotten_interpretations(store: Store):
    mem = store.remember("inferred summary", tier="interpretation")
    store.forget(mem.id, reason="superseded by a better summary")
    stale = store.due_for_review(days=0)
    assert not any(r["id"] == mem.id for r in stale)
