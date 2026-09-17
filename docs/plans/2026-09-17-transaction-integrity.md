# Transaction Integrity Implementation Plan

> Execution: use the executing-plans and single-flow-task-execution skills in this task, with test-driven development and separate specification/quality review passes.

**Goal:** Make each Store state transition atomic across SQLite connections and roll back incomplete business/audit writes on failure.

**Architecture:** Keep the per-instance RLock for connection safety. Introduce an explicit transaction boundary for state-changing methods, reserving SQLite's writer before reading preconditions (`BEGIN IMMEDIATE`). Nested calls share the outer transaction; successful outer calls commit, unexpected failures roll back. Deliberate pin rejection must still persist its audit event and expose the existing PermissionError contract.

**Tech stack:** Python 3.10+, sqlite3, pytest/anyio, MCP 1.x/2.x. No schema migration or new runtime dependency.

## Scope and alternatives

1. Recommended: explicit transaction ownership in the storage layer. This covers guards, state changes, and audit records together, including calls from different processes.
2. Database uniqueness constraints alone prevent some duplicate rows but do not protect the pin/forget invariant or failed audit writes; defer constraint migrations and legacy repair.
3. A Python process-wide lock does not protect separate MCP server processes; retain RLock only for the individual connection.

Source-tier semantics, unpin's reason/audit API, and narrative propagation policy remain separate roadmap items. This stage changes transaction integrity, not those protocol decisions.

## Task 1: Reproduce existing failures

Create `tests/test_transactions.py`.

1. Use SQLite audit triggers that raise ABORT to fail after business writes. Assert complete table snapshots remain unchanged after the error and a subsequent successful call, including narrative links, canon memberships, and earlier audit entries.
2. Exercise a late failure in a multi-memory scope operation and in interpretation status refresh.
3. Use two independent Store connections and SQLite trace callbacks/events to pause one operation after guard reads but before its first write. A contender either completes first (the broken implementation) or reaches its early writer reservation (the corrected implementation). Do not use sleeps as the race trigger.
4. Reproduce duplicate current narratives, duplicate canon memberships/open conflicts, and both pin/forget race orders.
5. Run `.venv/bin/python -m pytest tests/test_transactions.py -q --tb=short`; confirm failures represent the intended invariant violations before editing production code.

## Task 2: Implement transaction ownership

Modify `src/memory_as_history/storage.py`.

1. Add a private transaction decorator using the existing RLock and explicit outer ownership; use BEGIN IMMEDIATE before state validation reads.
2. Move commit/rollback out of individual write methods into the outer boundary. Reads called inside a transaction remain on the same connection and snapshot. `recall` is transactional because it refreshes review status.
3. Preserve a committed `pin_denied` audit through an explicit internal denial signal, converting it back to PermissionError for existing callers. Database/audit failures must roll back, including failures while writing a denial log.
4. Verify targeted failure and race tests, then all existing storage/protocol tests.
5. Perform specification review first, then inspect nested calls, exception paths, transaction lifetime, and clean connection reuse.

## Task 3: Verify real processes and ship a reviewable change

1. Add an independent-process race check with bounded events/joins and strict exit-code assertions. Ensure parent cleanup releases blocked workers on failure.
2. Validate intentional denied-pin audit persistence after reconnect, failed-denial audit rollback, nested recall rollback, and writer-lock timeout/recovery.
3. Run the full suite and `reliability_test.py`, `usefulness_test.py`, `poisoning_test.py` on the final code; verify MCP 1.2.0 and current 1.x/2.x.
4. Build wheel/sdist and check a clean installed wheel, with the same compatibility CI as PR #1.
5. Update CHANGELOG and the roadmap/tracker, commit, and open a draft PR based on `codex/reliability-baseline`, so its diff contains only this transaction stage. Do not merge or publish a release in this task.

## Acceptance

- No partial business/audit mutations survive failure or become visible after a later call commits.
- Each raced operation preserves its documented invariant, rather than merely avoiding a crash.
- A rejected sensitive pin retains exactly its intentional denial audit, without creating an anchor.
- Connections remain usable after ordinary errors and lock contention.
- Existing 128 tests still pass; no schema change and no additional runtime dependency.

## Implementation and review evidence

The first 1.2 stage is implemented as `1.2.0.dev0`. The new transaction suite
has 28 cases: 27 expose failures on the 1.1.1 implementation, while the
intentional-denial case preserves existing behavior. All 28 pass after the fix.

Specification review: every business-writing method, including unpin and
review-status refresh, now uses the transaction boundary. Read helpers used
inside a call share that call's connection; nested review refresh cannot
commit before recall finishes. State guards, successful audit entries, and
returned data are computed in the same transaction.

Quality review: failures during method execution or commit roll back;
failure to acquire the writer leaves transaction ownership unset. The special
denial path is an internal signal raised before business mutation and converted
back to an ordinary PermissionError only after its audit commits. No generic
PermissionError is treated as a successful audited denial. Existing RLock
protection and the 30-second SQLite busy timeout remain.

Tradeoff: recall now reserves the writer for its duration because it refreshes
review state. The local 5,000-memory unqueried recall check took 19 ms; this is
one environment measurement, not a universal latency bound. Migration races
and repairs to pre-existing invalid data are outside this stage.

Local verification on macOS / Python 3.12.13:

| Check | Result |
| --- | --- |
| MCP 2.2.0 | 156/156 tests passed |
| MCP 1.30.0 | 156/156 tests passed |
| MCP 1.2.0 | 156/156 tests passed |
| Reliability battery | 6/6 passed |
| Mechanism scripts | Poisoning check passed; usefulness outputs matched expectations |
| Packaging | wheel and sdist built; clean installed wheel passed all 156 tests from outside the checkout |
| Diff / CI configuration | Whitespace check and YAML parsing passed |

Review delivery: [PR #2](https://github.com/BrunoBanana/memory-as-history/pull/2)
is based on [PR #1](https://github.com/BrunoBanana/memory-as-history/pull/1).
PR #1's nine checks passed at `7dd0bce`. PR #2's current-head CI checks are
the authoritative remote validation record. Both stages remain unmerged;
source-tier semantics and unpin audit design are the next P1 roadmap items.
