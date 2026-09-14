# Memory as History

> Most agent memory systems decide what to keep with recency and similarity scores. This project treats agent memory the way memory studies treats human memory: memory becomes history through **deliberate consolidation** and **anchored identity** — not just storage and retrieval.

**Status: v0.1 — local prototype, not yet published.**

## Why

Current agent memory systems (Mem0, Letta, Zep, Cognee, ...) are very good at storage and retrieval. But they share one blind spot: *what gets remembered is decided by a score* — importance weight, recency, embedding similarity, decay curves.

Memory studies (Halbwachs, Nora, Assmann, Ricoeur) has spent a century describing how human memory actually becomes durable, and the answer is never "the highest-scoring facts survive automatically":

- Memory is **socially framed**, not a private recording (Halbwachs).
- Identity anchors on a small set of **sites of memory** — *lieux de mémoire* — that don't compete with ordinary recollection (Nora).
- Durable ("cultural") memory is reached through an explicit **consolidation** process out of everyday ("communicative") memory — a ceremony, not a threshold (Assmann).
- Memory is layered into **archive / testimony / interpretation**, and forgetting is treated as necessary and legitimate, not a failure (Ricoeur).

Agent memory today has the storage. It is missing the historiography — the accountable process by which something becomes "remembered" rather than just "logged."

## What (v0.1 scope)

Two modules, deliberately small:

| Module | Mechanism | Source theory |
|---|---|---|
| **Consolidation** | Memories start as `working`. They only become `consolidated` through an explicit `promote(reason)` call — never automatically. Every promotion is logged with its reason. | Assmann: communicative → cultural memory |
| **Anchors** | A small set of `pin(reason)`-ed memories. Anchors are always surfaced on recall, in full, regardless of query — they do not compete on relevance or recency. | Nora: *lieux de mémoire* |

Not in v0.1 (planned, not yet built): provenance tiers (archive/testimony/interpretation), accountable forgetting.

## Distribution

MCP Server, Python. Designed to sit as a protocol layer — not a replacement for a storage/embedding backend. v0.1 uses plain SQLite with no embedding dependency by design (recall is anchors-first + substring match); a real backend can be swapped in later without changing the protocol surface.

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

- `remember(content, source?)` — store a working memory
- `promote(memory_id, reason)` — consolidate a working memory (reason required)
- `pin(memory_id, reason)` — mark a memory as an anchor (reason required)
- `unpin(memory_id)` — remove anchor status (memory itself is kept)
- `recall(query?, limit?)` — anchors first, then consolidated, then working memories
- `consolidation_log(limit?)` — full audit trail of every promote/pin decision

## Related work

- **HistoRAG** (2026) — applies historiographical method to RAG for *human history research*. This project applies memory studies to *agent memory architecture itself* — a different target.
- **SOUL.md / identity-continuity grassroots ecosystem** — real, growing demand for agent identity persistence with almost no theoretical grounding. This project aims to supply that grounding as a concrete, testable protocol rather than another slogan-driven convention.

---

*Local prototype. Not yet pushed to a public remote.*
