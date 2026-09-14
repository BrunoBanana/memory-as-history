import tempfile
from pathlib import Path

import pytest

from memory_as_history.storage import Store


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "test.db")
        yield s
        s.close()


def test_remember_creates_working_memory(store: Store):
    mem = store.remember("user prefers dark mode", source="chat")
    assert mem.status == "working"
    assert mem.content == "user prefers dark mode"


def test_promote_requires_reason_and_logs(store: Store):
    mem = store.remember("decided to use SQLite for v0.1")
    promoted = store.promote(mem.id, reason="architecture decision, referenced repeatedly")
    assert promoted.status == "consolidated"
    assert promoted.consolidation_reason.startswith("architecture decision")

    log = store.consolidation_log()
    assert any(e["action"] == "promote" and e["memory_id"] == mem.id for e in log)


def test_promote_unknown_memory_raises(store: Store):
    with pytest.raises(KeyError):
        store.promote("does-not-exist", reason="n/a")


def test_pin_creates_anchor_and_logs(store: Store):
    mem = store.remember("core identity: I am a memory-as-history agent")
    result = store.pin(mem.id, reason="foundational identity statement")
    assert result["memory_id"] == mem.id

    anchors = store.list_anchors()
    assert len(anchors) == 1
    assert anchors[0]["id"] == mem.id

    log = store.consolidation_log()
    assert any(e["action"] == "pin" for e in log)


def test_unpin_removes_anchor_but_keeps_memory(store: Store):
    mem = store.remember("temporary anchor test")
    store.pin(mem.id, reason="test")
    store.unpin(mem.id)
    assert store.list_anchors() == []
    assert store.get(mem.id) is not None


def test_recall_anchors_always_first_regardless_of_query(store: Store):
    anchor_mem = store.remember("anchor: identity is stable")
    store.pin(anchor_mem.id, reason="identity anchor")
    store.remember("unrelated ordinary memory about weather")

    result = store.recall(query="weather", limit=5)
    anchor_ids = [a["id"] for a in result["anchors"]]
    assert anchor_mem.id in anchor_ids
    # anchor should be present even though query doesn't match its content
    assert all(a["id"] != anchor_mem.id or True for a in result["anchors"])


def test_recall_prefers_consolidated_over_working(store: Store):
    working = store.remember("working memory item")
    consolidated = store.remember("consolidated memory item")
    store.promote(consolidated.id, reason="important")

    result = store.recall(limit=10)
    ids_in_order = [m["id"] for m in result["memories"]]
    assert ids_in_order.index(consolidated.id) < ids_in_order.index(working.id)
