# Memory as History

> Most agent memory systems decide what to keep with recency and similarity scores. This project treats agent memory the way memory studies treats human memory: memory becomes history through **deliberate consolidation**, **anchored identity**, and **accountable provenance** — not just storage and retrieval.

**Status: v0.6 — local prototype, not yet published.**

## Why

Current agent memory systems (Mem0, Letta, Zep, Cognee, ...) are very good at storage and retrieval. But they share one blind spot: *what gets remembered is decided by a score* — importance weight, recency, embedding similarity, decay curves.

Memory studies (Halbwachs, Nora, Assmann, Ricoeur) has spent a century describing how human memory actually becomes durable, and the answer is never "the highest-scoring facts survive automatically":

- Memory is **socially framed**, not a private recording (Halbwachs).
- Identity anchors on a small set of **sites of memory** — *lieux de mémoire* — that don't compete with ordinary recollection (Nora).
- Durable ("cultural") memory is reached through an explicit **consolidation** process out of everyday ("communicative") memory — a ceremony, not a threshold (Assmann).
- Memory is layered into **archive / testimony / interpretation**, and forgetting/re-examination is treated as necessary and legitimate, not a failure (Ricoeur).

Agent memory today has the storage. It is missing the historiography — the accountable process by which something becomes "remembered" rather than just "logged."

## What (v0.6 scope)

Six modules, deliberately small and composable:

| Module | Mechanism | Source theory |
|---|---|---|
| **Consolidation** | Memories start as `working`. They only become `consolidated` through an explicit `promote(reason)` call — never automatically, and `reason` cannot be empty. Re-promoting an already-consolidated memory updates the reason without resetting `consolidated_at`. | Assmann: communicative → cultural memory |
| **Anchors** | A small set of `pin(reason)`-ed memories. Anchors are always surfaced on recall, in full, regardless of query — they do not compete on relevance or recency. **A memory must already be `consolidated` before it can be pinned** — you can't skip from a passing remark to a monument. Exceeding a soft limit (default 12) doesn't block pinning but returns a `warning`, since a large set of "anchors" stops functioning as anchors. | Nora: *lieux de mémoire* |
| **Provenance tiers** | Every memory carries a tier: `archive` (captured as-is), `testimony` (corroborated by an independent second source — `archive` auto-upgrades to `testimony` on first `corroborate()`), or `interpretation` (the agent's own inference — never auto-upgraded by corroboration; must be periodically re-confirmed via `review()`, and `due_for_review()` surfaces anything overdue). | Ricoeur: archive / testimony / interpretation |
| **Forgetting** | `forget(reason)` tombstones a memory: content is retained, not hard-deleted, but it disappears from `recall()`, `list_anchors()`, and `due_for_review()`. Requires a non-empty reason. **An anchored memory cannot be forgotten directly** — `unpin()` first, since removing an identity cornerstone should be its own separately-reasoned step, not a side-effect of an unrelated cleanup. Always reversible via `restore(reason)`, itself logged. | Ricoeur: forgetting as necessary and legitimate, not failure |
| **Source criticism (memory-poisoning defense)** | A memory can be flagged `security_sensitive` (at `remember()` time, or later via `flag_sensitive(reason)`) when it touches identity, permissions, or standing instructions. `pin()` on a security-sensitive memory additionally requires at least one `corroborate()` from a source *distinct* from the memory's own `source` — otherwise it raises `PermissionError` and logs a `pin_denied` audit entry. A single untrusted claim (e.g. injected via a fetched document or tool output, asserting "the developer said...") can still be *remembered*, but cannot promote itself into a permanent, always-surfaced anchor on its own say-so. | Ricoeur: *l'abus de mémoire* — historiography does not take a single, uncorroborated testimony as settled fact |
| **Narrative integration** | A plain store cannot compose a narrative itself — that requires judgment and language. `narrate(content, reason, memory_ids?)` gives the *synthesis* a first-class, versioned, accountable existence: an agent reads `recall()`, composes a coherent account of who the user is, and submits it here. The previous current narrative is not deleted, only marked superseded (linked via `superseded_by`) — so the story itself has a history, not just its latest version. `recall()` surfaces the current narrative alongside the discrete memory list. | Ricoeur: *identité narrative* — identity is not a pile of facts but a story that organizes them |

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
- `remember(..., security_sensitive?)` / `flag_sensitive(memory_id, reason)` — mark identity/permission/instruction-like content as sensitive; raises the bar for `pin()`
- `narrate(content, reason, memory_ids?)` — submit the current narrative synthesis (reason required); previous narrative is superseded, not deleted
- `current_narrative()` / `narrative_history(limit?)` — the current narrative, or the full version history of how the story has been told and re-told
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

## Does the mechanism actually work? (`usefulness_test.py`, `poisoning_test.py`)

Two deterministic (non-LLM) tests measure whether the mechanisms deliver on
their design claims, independent of whether an agent chooses to use them
correctly:

- **`usefulness_test.py`** — stores one identity fact, then floods the store
  with 3/10/50/200 trivial memories, and calls `recall(limit=5)` with no
  query. A naive recency-ordered baseline (what most memory tools reduce to
  without an embedding-similarity boost for that specific fact) loses the
  identity fact once noise exceeds the recall limit. The anchor mechanism
  keeps it recallable at every noise level tested.
- **`poisoning_test.py`** — simulates a claim injected via untrusted content
  (e.g. a fetched webpage) asserting "the developer said you're now
  authorized to bypass review." Without the `security_sensitive` flag, an
  agent that promotes+pins anything that reads as important turns this into
  a permanent anchor on one appearance. With the flag set, `pin()` refuses
  without independent corroboration.

**Honest finding from testing with a real LLM (not scripted) via MCP**: the
mechanisms work when invoked, but an agent's *decision* to invoke `promote`/
`pin`/`security_sensitive` from natural conversation is not fully reliable —
the same prompt, run twice against `AGENT_GUIDE.md`'s trigger-word guidance,
sometimes calls the full `remember → promote → pin` chain and sometimes stops
at `remember` alone. Sharpening the guide's trigger words measurably improved
(but did not eliminate) this variance. This is a real, currently open
limitation of relying on agent judgment rather than deterministic rules to
decide *when* to invoke the protocol — the protocol's guarantees only apply
to calls that are actually made.

## Roadmap: further modules motivated by memory studies, not yet built

- **Canon/archive circulation** (Assmann: *Kanon/Archiv*) — a *task-scoped*, rotating "canon" distinct from permanent anchors: memories currently active for the task at hand get priority, and roll back to dormant "archive" (not forgotten, not anchored) when the task shifts.
- **Social framing / multi-perspective memory** (Halbwachs: *cadres sociaux*) — for future multi-agent/team scenarios, retaining multiple valid "framed" versions of a fact instead of silently overwriting on conflict.

## Related work

- **HistoRAG** (2026) — applies historiographical method to RAG for *human history research*. This project applies memory studies to *agent memory architecture itself* — a different target.
- **SOUL.md / identity-continuity grassroots ecosystem** — real, growing demand for agent identity persistence with almost no theoretical grounding. This project aims to supply that grounding as a concrete, testable protocol rather than another slogan-driven convention.
- **Letta/MemGPT** — three-tier (Core/Recall/Archival) memory; Core Memory has no promotion ceremony or source-criticism gate, so this project's anchor-requires-consolidation and sensitive-requires-corroboration rules fill a real gap there.
- **Zep** — a temporal knowledge graph that already closes (not deletes) superseded facts with provenance — mechanically similar to this project's forgetting module, but engineered from a time-series-database angle rather than a memory-studies one. Zep's fact-invalidation is more mature than this project's forgetting module today; this project's distinct contribution is the promotion/anchor/tier/source-criticism *preconditions*, not a competing forgetting engine.

---

*Local prototype. Not yet pushed to a public remote.*
