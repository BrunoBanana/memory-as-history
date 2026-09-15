# Memory as History

> Most agent memory systems decide what to keep with recency and similarity scores. This project treats agent memory the way memory studies treats human memory: memory becomes history through **deliberate consolidation**, **anchored identity**, and **accountable provenance** — not just storage and retrieval.

**Status: v0.3 — local prototype, not yet published.**

## Why

Current agent memory systems (Mem0, Letta, Zep, Cognee, ...) are very good at storage and retrieval. But they share one blind spot: *what gets remembered is decided by a score* — importance weight, recency, embedding similarity, decay curves.

Memory studies (Halbwachs, Nora, Assmann, Ricoeur) has spent a century describing how human memory actually becomes durable, and the answer is never "the highest-scoring facts survive automatically":

- Memory is **socially framed**, not a private recording (Halbwachs).
- Identity anchors on a small set of **sites of memory** — *lieux de mémoire* — that don't compete with ordinary recollection (Nora).
- Durable ("cultural") memory is reached through an explicit **consolidation** process out of everyday ("communicative") memory — a ceremony, not a threshold (Assmann).
- Memory is layered into **archive / testimony / interpretation**, and forgetting/re-examination is treated as necessary and legitimate, not a failure (Ricoeur).

Agent memory today has the storage. It is missing the historiography — the accountable process by which something becomes "remembered" rather than just "logged."

## What (v0.3 scope)

Four modules, deliberately small and composable:

| Module | Mechanism | Source theory |
|---|---|---|
| **Consolidation** | Memories start as `working`. They only become `consolidated` through an explicit `promote(reason)` call — never automatically, and `reason` cannot be empty. Re-promoting an already-consolidated memory updates the reason without resetting `consolidated_at`. | Assmann: communicative → cultural memory |
| **Anchors** | A small set of `pin(reason)`-ed memories. Anchors are always surfaced on recall, in full, regardless of query — they do not compete on relevance or recency. **A memory must already be `consolidated` before it can be pinned** — you can't skip from a passing remark to a monument. Exceeding a soft limit (default 12) doesn't block pinning but returns a `warning`, since a large set of "anchors" stops functioning as anchors. | Nora: *lieux de mémoire* |
| **Provenance tiers** | Every memory carries a tier: `archive` (captured as-is), `testimony` (corroborated by an independent second source — `archive` auto-upgrades to `testimony` on first `corroborate()`), or `interpretation` (the agent's own inference — never auto-upgraded by corroboration; must be periodically re-confirmed via `review()`, and `due_for_review()` surfaces anything overdue). | Ricoeur: archive / testimony / interpretation |
| **Forgetting** | `forget(reason)` tombstones a memory: content is retained, not hard-deleted, but it disappears from `recall()`, `list_anchors()`, and `due_for_review()`. Requires a non-empty reason. **An anchored memory cannot be forgotten directly** — `unpin()` first, since removing an identity cornerstone should be its own separately-reasoned step, not a side-effect of an unrelated cleanup. Always reversible via `restore(reason)`, itself logged. | Ricoeur: forgetting as necessary and legitimate, not failure |

All state-changing actions (`promote`, `pin`, `corroborate` when it upgrades, `review`, `forget`, `restore`) are written to a single `audit_log` with the reason/note and timestamp — every accountable decision about what became history, and why, is queryable.

## Distribution

MCP Server, Python. Designed to sit as a protocol layer — not a replacement for a storage/embedding backend. v0.2 uses plain SQLite with no embedding dependency by design (recall is anchors-first + substring match); a real backend can be swapped in later without changing the protocol surface.

## Quick start (local)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e .
python -m pytest tests/ -v
```

Run the MCP server directly:

```bash
python -m memory_as_history.server
```

Or point an MCP-compatible client (Claude Code, Cursor) at it via stdio.

### Tools exposed

- `remember(content, source?, tier?)` — store a memory (`tier`: `archive` default, `testimony`, or `interpretation`)
- `promote(memory_id, reason)` — consolidate a working memory (reason required)
- `pin(memory_id, reason)` — anchor a **consolidated** memory (reason required; must `promote()` first)
- `unpin(memory_id)` — remove anchor status (memory itself is kept)
- `corroborate(memory_id, source)` — record an independent source; `archive` → `testimony` on first call
- `review(memory_id, note)` — re-confirm an `interpretation`-tier memory, resets its review clock
- `due_for_review(days?)` — list `interpretation` memories overdue for re-examination (default: 30 days)
- `forget(memory_id, reason)` — tombstone a memory (reason required; must `unpin()` first if anchored)
- `restore(memory_id, reason)` — reverse a forgetting decision (reason required)
- `list_forgotten(limit?)` — list tombstoned memories and why
- `recall(query?, limit?)` — anchors first, then consolidated, then working memories; also returns `stale_interpretations`
- `audit_log(limit?)` — full trail of promote/pin/corroborate/review/forget/restore decisions, with reasons

## Reliability

`reliability_test.py` runs a battery of robustness checks beyond the unit tests:
thread-safety (concurrent tool calls against one `Store`), multi-process
concurrency (same sqlite file from separate processes), persistence across
connection restarts, scale (5,000 memories, sub-50ms `recall()`), and an edge-case
suite (1MB content, unicode, SQL-injection-shaped strings, empty/negative inputs,
nonexistent ids, double-forget). All 6 checks pass.

One real bug was found and fixed this way: the original `Store` used a bare
`sqlite3.connect()`, which raised `ProgrammingError` under concurrent access from
multiple threads (a single `Store` instance is shared across an MCP server's
concurrent tool-call handlers). Fixed with `check_same_thread=False` plus an
instance-level `threading.RLock()` serializing all public methods — sqlite3
connections are not safe for concurrent use even with that flag alone.

## Related work

- **HistoRAG** (2026) — applies historiographical method to RAG for *human history research*. This project applies memory studies to *agent memory architecture itself* — a different target.
- **SOUL.md / identity-continuity grassroots ecosystem** — real, growing demand for agent identity persistence with almost no theoretical grounding. This project aims to supply that grounding as a concrete, testable protocol rather than another slogan-driven convention.

---

*Local prototype. Not yet pushed to a public remote.*
