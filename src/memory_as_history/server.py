"""MCP Server exposing the Memory as History protocol.

Tools:
  - remember(content, source?)         store a working memory
  - promote(memory_id, reason)         consolidate a working memory (requires reason)
  - pin(memory_id, reason)             mark a memory as an anchor (requires reason)
  - unpin(memory_id)                   remove anchor status
  - recall(query?, limit?)             anchors first, then consolidated/working memories
  - consolidation_log(limit?)          audit trail of promote/pin actions

Environment:
  MEMORY_AS_HISTORY_DB   path to the sqlite db (default: ~/.memory-as-history/memory.db)
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .storage import DEFAULT_DB_PATH, Store

_db_path = os.environ.get("MEMORY_AS_HISTORY_DB", str(DEFAULT_DB_PATH))
store = Store(_db_path)

mcp = FastMCP("memory-as-history")


@mcp.tool()
def remember(content: str, source: str | None = None) -> dict:
    """Store a new working memory. Working memories are ordinary recollections
    that have not yet gone through consolidation — they can still be recalled,
    but they compete on recency, not on declared importance."""
    return store.remember(content, source).to_dict()


@mcp.tool()
def promote(memory_id: str, reason: str) -> dict:
    """Consolidate a working memory into long-term memory. This is a
    deliberate, auditable act — not a similarity/importance score threshold.
    `reason` is required and becomes part of the consolidation log."""
    return store.promote(memory_id, reason).to_dict()


@mcp.tool()
def pin(memory_id: str, reason: str) -> dict:
    """Mark a memory as an anchor: a 'site of memory' that is always surfaced
    on recall and never competes with ordinary memories on recency or
    relevance. `reason` is required — anchors are declared, not inferred."""
    return store.pin(memory_id, reason)


@mcp.tool()
def unpin(memory_id: str) -> dict:
    """Remove anchor status from a memory. The memory itself is not deleted."""
    store.unpin(memory_id)
    return {"memory_id": memory_id, "unpinned": True}


@mcp.tool()
def recall(query: str | None = None, limit: int = 10) -> dict:
    """Recall memories. Anchors are always returned in full regardless of
    query. Remaining slots are filled by consolidated memories first, then
    working memories, newest first, optionally filtered by `query`."""
    return store.recall(query, limit)


@mcp.tool()
def consolidation_log(limit: int = 50) -> list[dict]:
    """Return the audit trail of promote/pin actions — every accountable
    decision about what became history, and why."""
    return store.consolidation_log(limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
