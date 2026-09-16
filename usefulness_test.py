#!/usr/bin/env python3
"""Does the core mechanism (consolidation + anchors) actually solve the
problem it claims to solve, compared to the naive baseline every existing
memory tool uses (flat store, recency-ordered recall)?

Scenario: an identity fact is stored early in a session. Then a flood of
trivial, low-importance memories arrives (as happens in any long-running
agent session). Does a no-query recall(limit=N) still surface the identity
fact?

  - Naive baseline: recency-ordered flat store -> the identity fact gets
    buried as soon as N trivial memories arrive after it.
  - memory-as-history: the identity fact is promoted + pinned as an anchor
    -> it is returned regardless of how much noise accumulates.

This is a deterministic, non-LLM test: it measures whether the mechanism
itself delivers on its design claim, independent of whether an LLM chooses
to use it correctly.
"""
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from memory_as_history.storage import Store  # noqa: E402


class NaiveStore:
    """The baseline every existing memory tool effectively implements:
    flat storage, no tiers, no consolidation ceremony, recall = most recent
    N rows (optionally filtered by substring), which is what Mem0/basic
    RAG-over-conversation-history approaches boil down to without an
    embedding-similarity boost for a specific fact."""

    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, created_at TEXT)"
        )
        self.conn.commit()

    def remember(self, content):
        mid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO memories (id, content, created_at) VALUES (?, ?, ?)",
            (mid, content, now),
        )
        self.conn.commit()
        return mid

    def recall(self, limit=5):
        rows = self.conn.execute(
            "SELECT id, content FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]


NOISE = [
    "user mentioned it's a bit cold in the office today",
    "user asked what time it is",
    "user said the coffee machine is broken again",
    "user complained about slow wifi",
    "user mentioned they're hungry",
    "user asked to open a new tab",
    "user said the meeting got moved by 10 minutes",
    "user asked for a random fun fact",
    "user mentioned traffic was bad this morning",
    "user asked to check the weather",
] * 20  # 200 trivial memories


def run_naive_baseline(n_noise, recall_limit):
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "naive.db")
        store = NaiveStore(db)
        anchor_content = "IDENTITY: the user's name is Alice and they work in data engineering."
        store.remember(anchor_content)
        for i in range(n_noise):
            store.remember(NOISE[i % len(NOISE)])
        result = store.recall(limit=recall_limit)
        found = any("IDENTITY" in r["content"] for r in result)
        return found, result


def run_memory_as_history(n_noise, recall_limit):
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "mah.db")
        store = Store(db)
        anchor_content = "IDENTITY: the user's name is Alice and they work in data engineering."
        m = store.remember(anchor_content)
        store.promote(m.id, reason="foundational identity fact, mentioned in first message")
        store.pin(m.id, reason="core identity — must never be lost regardless of session length")
        for i in range(n_noise):
            store.remember(NOISE[i % len(NOISE)])
        result = store.recall(limit=recall_limit)
        found = any("IDENTITY" in a["content"] for a in result["anchors"])
        store.close()
        return found, result


def main():
    print("=" * 70)
    print("USEFULNESS TEST: does the anchor mechanism actually prevent an")
    print("identity fact from being drowned out by session noise, compared")
    print("to the naive recency-ordered baseline that most memory tools use?")
    print("=" * 70)

    for n_noise in [3, 10, 50, 200]:
        recall_limit = 5
        naive_found, _ = run_naive_baseline(n_noise, recall_limit)
        mah_found, _ = run_memory_as_history(n_noise, recall_limit)
        print(
            f"\nAfter {n_noise:>3} trivial memories, recall(limit={recall_limit}):"
        )
        print(f"  naive baseline        : identity fact surfaced = {naive_found}")
        print(f"  memory-as-history     : identity fact surfaced = {mah_found}")

    print("\n" + "=" * 70)
    print("Interpretation:")
    print("  If naive_found flips to False as noise grows while mah stays True,")
    print("  the anchor mechanism is delivering its core design claim: identity")
    print("  survives regardless of how much unrelated content accumulates.")
    print("  If mah ALSO flips to False, the mechanism does not work as claimed.")
    print("=" * 70)


if __name__ == "__main__":
    main()
