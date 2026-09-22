# Cross-session MCP acceptance plan

## Scope

Merge the verified provenance/audit PR, then prove the complete memory lifecycle
across independent client and server processes. Use fictional fixtures and a
separate database; leave normal client configuration and user memories intact.
This batch validates existing behavior and documents integration. Change
production code only if a concrete defect is reproduced.

## Tasks

1. Confirm PR #3's current head and all nine CI jobs, merge that exact head,
   and create an isolated worktree from merged main. Establish the 177-test baseline.
2. Add deterministic MCP stdio acceptance to the normal suite. Capture a
   sensitive memory, consolidate it, verify insufficient/same-source evidence
   is denied, corroborate independently, and pin. Shut down the client/server;
   start a new pair on the same database, rediscover the memory through recall,
   check provenance, and unpin with a reason. Restart again and confirm the
   memory remains recallable, no anchor returns, and audit outcomes persisted.
   Cover known and unknown origins. Verify the test detects state loss using
   an in-memory database negative control without changing production code.
3. Run the same bounded fixture through three fresh Codex CLI invocations,
   using the configured model, ephemeral sessions, and a per-invocation MCP
   override. Only the database is shared; later prompts do not contain the
   stored fact or memory ID. Inspect tool events and persisted data, not just
   the client's final prose. This is an explicit tool-use acceptance check,
   not a claim about spontaneous memory usage or a broad LLM evaluation.
4. Record reproducible commands, outcomes and limits. Run all tests across
   MCP 1.2.0/current 1.x/current 2.x, verify an installed wheel, and submit a
   reviewable PR with current-head CI. Do not publish a package release.

## Acceptance boundaries

- Every fresh protocol session launches and later terminates a server process.
- Sensitive pin denial and successful evidence/anchor transitions survive restart.
- Recall in a new session discovers the original memory without receiving its ID.
- Unpin's reason and exactly one actual-removal audit survive another restart;
  ordinary memory is retained, and an idempotent repeat logs a distinct no-op.
- Codex's JSONL tool events and independent read-only database checks agree.
- Hosted-model acceptance is documented separately from deterministic CI tests;
  no model credentials or network calls are required by pytest.


## Results and review

PR #3 merged as `8aa14eb` after its exact head `a3a6d40` passed all nine CI jobs.
Merged-main baseline: 177/177. The new `tests/test_sessions.py` adds two cases,
each covering four fully closed/reopened stdio client/server contexts. Both
negative controls failed at the first post-restart anchor assertion when their
launcher used an in-memory database; production code was never modified.

Specification review: fresh sessions rediscover IDs through recall, sensitive
pinning obeys the known/unknown-origin gate, and unpin preserves the ordinary
memory while its exact reason and subsequent legacy no-op survive restarts.
The test asserts persisted denial, corroboration, upgrade, pin and unpin counts.

Quality review: temporary databases are isolated per case, subprocess lifecycle
is owned by async context managers, every session has a bounded timeout, and
list payload normalization supports MCP 1.x/2.x. No new dependency, network
requirement in pytest, schema change or runtime behavior was introduced.

Three independent Codex conversations passed the guided fixture: 15 completed
MCP calls, later prompts containing neither the saved fact nor its ID, and a
read-only database check of one memory, zero anchors, two evidence records and
seven audit entries. The CLI needed its application-bundled executable, the
existing system proxy passed to the subprocess, and explicit approval for the
allow-listed test server's tools. Failed setup attempts did not create memories;
normal user configuration and the real memory database were left untouched.
See [the acceptance guide and evidence](../acceptance/cross-session.md).

All 179 tests pass on MCP 1.2.0, 1.30.0 and 2.2.0. Wheel/sdist build successfully;
a clean wheel installed outside the checkout also passes 179/179. Review delivery: PR #4 (historical; PR refs were removed in the 2026-09-22 repository recreation, see CHANGELOG).
Its current-head CI checks are the authoritative remote validation record. This batch leaves the runtime
at `1.2.0.dev0` and does not publish a release.
