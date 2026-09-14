"""MCP Server exposing the Memory as History protocol.

Modules:
  Consolidation (Assmann) — remember() / promote(reason)
  Anchors (Nora)          — pin(reason) / unpin() / list_anchors()
  Provenance tiers (Ricoeur) — tier at remember(), corroborate(), review(),
                                due_for_review()

Tools:
  - remember(content, source?, tier?)   store a memory (tier: archive|testimony|interpretation)
  - promote(memory_id, reason)          consolidate a working memory (requires reason)
  - pin(memory_id, reason)              anchor a *consolidated* memory (requires reason)
  - unpin(memory_id)                    remove anchor status
  - corroborate(memory_id, source)      record an independent source; archive -> testimony
  - review(memory_id, note)             re-confirm an interpretation-tier memory
  - due_for_review(days?)               list interpretation memories overdue for review
  - recall(query?, limit?)              anchors first, then consolidated/working memories
  - audit_log(limit?)                   full trail of promote/pin/corroborate/review actions

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
def remember(content: str, source: str | None = None, tier: str = "archive") -> dict:
    """Store a new working memory. Working memories are ordinary recollections
    that have not yet gone through consolidation — they can still be recalled,
    but they compete on recency, not on declared importance.

    `tier` defaults to 'archive' (captured as directly observed). Use
    tier='interpretation' when this is the agent's own inference/summary
    rather than an observed fact — it will be scheduled for periodic review."""
    return store.remember(content, source, tier).to_dict()


@mcp.tool()
def promote(memory_id: str, reason: str) -> dict:
    """Consolidate a working memory into long-term memory. This is a
    deliberate, auditable act — not a similarity/importance score threshold.
    `reason` is required and becomes part of the audit log."""
    return store.promote(memory_id, reason).to_dict()


@mcp.tool()
def pin(memory_id: str, reason: str) -> dict:
    """Mark a memory as an anchor: a 'site of memory' that is always surfaced
    on recall and never competes with ordinary memories on recency or
    relevance. `reason` is required — anchors are declared, not inferred.

    The memory must already be consolidated (call `promote()` first) —
    anchors are built on things that have already become history, not on
    passing remarks. If the number of anchors exceeds a soft limit, the
    result includes a `warning`."""
    return store.pin(memory_id, reason)


@mcp.tool()
def unpin(memory_id: str) -> dict:
    """Remove anchor status from a memory. The memory itself is not deleted."""
    store.unpin(memory_id)
    return {"memory_id": memory_id, "unpinned": True}


@mcp.tool()
def corroborate(memory_id: str, source: str) -> dict:
    """Record that an independent additional source corroborates this memory.
    An 'archive' (single-source, raw) memory is automatically upgraded to
    'testimony' on first corroboration. Has no upgrade effect on
    'interpretation'-tier memories — use `review()` for those instead."""
    return store.corroborate(memory_id, source).to_dict()


@mcp.tool()
def review(memory_id: str, note: str) -> dict:
    """Re-examine an 'interpretation'-tier memory and confirm it still holds.
    Interpretation is inherently provisional in this protocol — it must be
    periodically revisited, not trusted indefinitely just because it was
    once inferred. Resets the review clock."""
    return store.review(memory_id, note).to_dict()


@mcp.tool()
def due_for_review(days: int | None = None) -> list[dict]:
    """List interpretation-tier memories overdue for re-examination (default
    threshold: 30 days since last review, or never reviewed)."""
    return store.due_for_review(days)


@mcp.tool()
def recall(query: str | None = None, limit: int = 10) -> dict:
    """Recall memories. Anchors are always returned in full regardless of
    query. Remaining slots are filled by consolidated memories first, then
    working memories, newest first, optionally filtered by `query`. Also
    returns `stale_interpretations` due for review."""
    return store.recall(query, limit)


@mcp.tool()
def audit_log(limit: int = 50) -> list[dict]:
    """Return the full audit trail — every promote/pin/corroborate/review
    action, with its reason and timestamp. Every accountable decision about
    what became history, and why."""
    return store.audit_log(limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
