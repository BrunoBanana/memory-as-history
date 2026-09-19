# Historical Claims Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Deliver the approved claim/evidence, knowledge-history, scoped-narrative
and archive-investigation capabilities as an unpublished 1.3.0a1 preview.

**Architecture:** Additive SQLite tables and columns, immutable claims with event
history, scoped narrative dependencies, existing transaction boundaries and MCP.

**Tech Stack:** Python 3.10+, sqlite3, pytest, MCP 1.x/2.x, hatchling.

---

The referenced Antigravity workflow is not present in this repository. Execute
the available single-flow, TDD and verification skills in this task. The user has
approved the direction; routine implementation choices do not require another
approval round. Working branch: `codex/historical-claims`.

## Task 1 — Claims, evidence and recording-time history

Files: `src/memory_as_history/{knowledge,transactions,storage}.py`,
`tests/test_knowledge.py`.

Write failing tests for material metadata, evidence specificity/common origins,
adoption/revision/withdrawal, late capture and current forgetting. Implement the
ledger and additive migrations. Add rollback, tied-time and concurrent-revision
regressions. Run `python -m pytest tests/test_knowledge.py -q`, then existing
storage/history/transaction tests. Review specification then quality and commit.

## Task 2 — Parallel narratives and archive investigation

Files: `src/memory_as_history/storage.py`, `tests/test_historical_views.py`.

Write failing tests for separate narrative chains and old default behavior,
claim/relationship invalidation, endpoint forgetting and counterevidence under
anchor pressure. Implement explicit dependencies, scope/perspective/coverage and
strict archive retrieval. Validate old database migration and independent scopes
under concurrency. Run both new test modules and all storage regression tests.
Review specification then quality and commit.

## Task 3 — MCP and end-to-end demonstration

Files: `src/memory_as_history/server.py`, `tests/test_knowledge_protocol.py`,
`examples/historical_claims.py`.

Write real-stdio tests before exposing tools. Exercise discovery, literal string
arguments, structured errors and a late-notice/revision lifecycle across process
restarts. Validate all affected dependency paths over the public protocol.
Run `python -m pytest tests/ -q` and the example; review and commit.

## Task 4 — Documentation, packaging and integration

Files: `pyproject.toml`, `CHANGELOG.md`, `README.md`, `README.zh-CN.md`,
`AGENT_GUIDE.md`, `docs/knowledge-history.md`, `docs/evaluation.md`,
`docs/plans/task.md`, `.github/workflows/ci.yml` if needed.

Correct simplified theoretical attribution, document exact contracts and limits,
and add migration/API examples. Preserve existing evaluation results and describe
new conformance checks separately from model quality. Test MCP minimum/1.x/2.x,
full reliability/protocol batteries and a wheel installed outside the checkout.
Review full diff, create PR, verify its exact-head checks, merge under established
authorization, sync main and verify main CI. No PyPI publication.
