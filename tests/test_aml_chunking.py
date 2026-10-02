"""Tests for AML primary-source chunking (P3, memory-as-history).

Long messages are divided at sentence boundaries into complete blocks
that share the original event metadata, so the source stays
reconstructable and every semantic segment is independently
retrievable. Short messages must pass through untouched.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from memory_as_history.aml import MemoryService
from memory_as_history.aml.chunking import split_long_message


def _sentence(n: int = 28) -> str:
    """One Chinese sentence of roughly n characters."""
    return "天气很好我们决定去公园散步顺便买些新鲜水果带回家做晚餐。"[:n]


# -- split_long_message ---------------------------------------------------

def test_short_message_untouched():
    text = "她去年住在杭州。"
    assert split_long_message(text) == [text]


def test_empty_and_whitespace_pass_through():
    assert split_long_message("") == [""]
    assert split_long_message("   ") == ["   "]


def test_long_message_splits_at_sentence_boundaries():
    text = "".join(_sentence() for _ in range(60))  # ~1680 chars
    blocks = split_long_message(text, max_chars=800, min_chars=200)
    assert len(blocks) >= 2
    # every block is a concatenation of complete sentences: it ends with
    # a sentence terminator and reconstructs the original in order
    assert "".join(blocks) == text
    for b in blocks:
        assert b.strip().endswith("。")


def test_blocks_respect_min_size():
    text = "".join(_sentence() for _ in range(60))
    blocks = split_long_message(text, max_chars=400, min_chars=200)
    assert len(blocks) >= 3
    for b in blocks[:-1]:
        assert len(b) >= 200


def test_no_sentence_boundary_keeps_source_whole():
    text = "这是一个没有句号的超长句子" * 100  # no 。！？；.!?\n at all
    assert split_long_message(text, max_chars=300) == [text]


def test_newline_boundary_respected():
    text = ("第一段内容。" * 120) + "\n" + ("第二段内容。" * 120)
    blocks = split_long_message(text, max_chars=800, min_chars=100)
    assert len(blocks) >= 2
    assert "".join(blocks) == text


# -- integration through MemoryService.add --------------------------------

def _service():
    data_dir = Path(tempfile.mkdtemp(prefix="aml-chunk-"))
    return MemoryService(data_dir=data_dir, search_mode="lexical")


def _add(service, messages, request_id="r1", session_id="s1", user_id="u1"):
    return service.add(
        {
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "messages": messages,
        }
    )


def test_short_messages_stay_one_row_per_message():
    svc = _service()
    _add(svc, [{"role": "user", "content": "她住在杭州。"},
               {"role": "user", "content": "他养了一只猫。"}])
    conn = sqlite3.connect(next(iter(Path(svc.data_dir / "users").glob("*.db"))))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT content FROM memories").fetchall()
        assert len(rows) == 2
        assert rows[0]["content"] == "她住在杭州。"
        assert rows[1]["content"] == "他养了一只猫。"
    finally:
        conn.close()


def test_long_message_becomes_blocks_sharing_metadata():
    svc = _service()
    long_text = "".join(_sentence() for _ in range(60))
    _add(svc, [{"role": "user", "content": long_text}])
    conn = sqlite3.connect(next(iter(Path(svc.data_dir / "users").glob("*.db"))))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT content, event_at, session_id, session_position "
            "FROM memories ORDER BY session_position"
        ).fetchall()
        assert len(rows) >= 2
        assert "".join(r["content"] for r in rows) == long_text
        assert len({r["session_id"] for r in rows}) == 1
        assert rows[0]["session_position"] == 0
        # positions are contiguous
        positions = [r["session_position"] for r in rows]
        assert positions == list(range(len(rows)))
    finally:
        conn.close()


def test_next_position_does_not_overlap_after_chunking():
    svc = _service()
    long_text = "".join(_sentence() for _ in range(60))
    _add(svc, [{"role": "user", "content": long_text}], request_id="r1")
    _add(svc, [{"role": "user", "content": "后来又搬到了上海。"}], request_id="r2")
    conn = sqlite3.connect(next(iter(Path(svc.data_dir / "users").glob("*.db"))))
    conn.row_factory = sqlite3.Row
    try:
        positions = [
            r["session_position"]
            for r in conn.execute(
                "SELECT session_position FROM memories ORDER BY session_position"
            ).fetchall()
        ]
        assert len(positions) == len(set(positions))  # no overlap
        assert positions == list(range(len(positions)))
        contents = conn.execute(
            "SELECT content FROM memories ORDER BY session_position"
        ).fetchall()
        assert contents[-1]["content"] == "后来又搬到了上海。"
    finally:
        conn.close()


def test_chunked_message_retrievable_by_block(monkeypatch):
    svc = _service()
    long_text = ("陈静去年住在杭州西湖边。") * 60 + ("王磊在深圳工作。") * 60
    _add(svc, [{"role": "user", "content": long_text}])

    # lexical search for a phrase living in the second block must hit that block
    res = svc.search({"query": "王磊在深圳工作", "user_id": "u1", "top_k": 10})
    contents = [r["content"] for r in res["data"]]
    assert any("王磊在深圳工作" in c for c in contents)
    # and a phrase in the first block hits a (possibly different) block
    res2 = svc.search({"query": "陈静住在西湖边", "user_id": "u1", "top_k": 10})
    contents2 = [r["content"] for r in res2["data"]]
    assert any("陈静去年住在杭州西湖边" in c for c in contents2)
