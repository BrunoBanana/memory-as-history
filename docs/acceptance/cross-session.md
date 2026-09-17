# Cross-session acceptance

This checks memory persistence through the real MCP protocol and a Codex client.
Use synthetic fixtures and a separate database. Later sessions receive neither
the saved memory ID nor its content; they must discover both through `recall()`.

## Verified result (2026-09-18)

Server main `8aa14eb`; macOS / Python 3.12.13 / MCP 2.2.0;
Codex CLI 0.155.0-alpha.2.6 with GPT-6 Astra at the configured xhigh effort.
Three fresh conversations completed 15 MCP tool calls after the setup corrections
below. Tool responses were asserted independently, then compared with a read-only
SQLite snapshot. [Recorded evidence](codex-evidence.json) contains only the
synthetic fixture and selected outcomes.

| Fresh session | Observed result |
| --- | --- |
| 1: capture and anchor | Unverified pin denied; same-source corroboration remains archive; independent evidence produces testimony and one anchor |
| 2: recall and unpin | Discovers the original ID/content from MCP alone; removes the anchor with a reason; memory remains recallable |
| 3: verify persistence | Zero anchors, one retained memory, sufficient provenance, and exactly one persisted unpin with the original reason/time |

The database contained one consolidated testimony, two corroboration records,
zero anchors and seven audit entries. No production-code defect was found in
this scenario. Deterministic tests passed 179/179 on MCP 1.2.0, 1.30.0 and 2.2.0,
and 179/179 from a clean installed wheel outside the checkout.

## Deterministic regression (no model or credentials)

```sh
python -m pytest tests/test_sessions.py -v
```

Two cases cover known and unknown origins. Each opens and closes four separate
client/server pairs against one temporary database:

1. Capture a sensitive memory, promote it, deny insufficient/same-source pinning,
   add independent corroboration, and pin it.
2. Restart, rediscover the anchor via recall, verify provenance, and unpin with a reason.
3. Restart, verify the ordinary memory remains, inspect the persisted denial,
   corroboration, upgrade, pin and unpin audits, then issue a legacy no-op unpin.
4. Restart and verify exactly one persisted no-op audit with the missing-reason marker.

A one-off negative control changed only the test launcher to use `:memory:`.
Both cases then failed on the first post-restart anchor check, confirming they
detect state loss. Production code was unchanged.

## Codex client acceptance

Prerequisites: install `.[test]` in `.venv`, log in to Codex, and choose an
accessible model. The tested CLI supports `--ephemeral` and
`--ignore-user-config` (check `codex exec --help`). Invocations below use a
per-process MCP configuration and a fresh conversation each time. Normal user
configuration is not edited; existing client authentication is reused.

Run from the repository root. Set `ACCEPTANCE_CODEX_BIN` to a working Codex
executable and `ACCEPTANCE_MODEL` to the model you intend to validate.

```sh
project_dir="$(pwd)"
trial_dir="$(mktemp -d)"
mkdir -p "$trial_dir/client"
server_python="$project_dir/.venv/bin/python"
mcp_override="mcp_servers.memory_acceptance={command=\"$server_python\",args=[\"-m\",\"memory_as_history.server\"],env={MEMORY_AS_HISTORY_DB=\"$trial_dir/fixture.db\"},required=true,default_tools_approval_mode=\"approve\",enabled_tools=[\"remember\",\"promote\",\"pin\",\"corroborate\",\"recall\",\"provenance\",\"unpin\",\"audit_log\"]}"

run_phase() {
  "$ACCEPTANCE_CODEX_BIN" --ask-for-approval never \
    --disable multi_agent --disable shell_tool --disable apps --disable plugins \
    --disable browser_use --disable computer_use \
    exec --ephemeral --ignore-user-config --skip-git-repo-check \
    --sandbox read-only --model "$ACCEPTANCE_MODEL" -c 'web_search="disabled"' \
    -c "$mcp_override" --cd "$trial_dir/client" --json \
    --output-last-message "$trial_dir/phase$1-final.json" - \
    < "$project_dir/docs/acceptance/prompts/phase$1.txt" \
    > "$trial_dir/phase$1.jsonl" 2> "$trial_dir/phase$1.stderr"
}

run_phase 1
# Inspect the final JSON and MCP events before continuing.
run_phase 2
run_phase 3
```

Use the explicit fixture-only tool approval above only with the separate test
database. Codex MCP supports stdio command/env configuration, tool allow lists,
and tool approval modes: [official MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The executed acceptance further restricted phase 2 to recall/provenance/audit/unpin
and phase 3 to recall/provenance/audit. No prior transcript or remembered ID was
passed into those phases.

A zero process exit is insufficient: require `status: "passed"`, inspect the
actual completed MCP tool calls, and compare against read-only database queries.
Phase 1 must show a structured pin denial, then testimony and an anchor. Phase 2
must discover and remove that anchor. Phase 3 must discover the same ordinary
memory and the exact persisted unpin reason. Inspect `memories`, `anchors`,
`corroborations`, and `audit_log` using SQLite read-only mode.

### Client setup lessons from this run

- A symlinked CLI entry point could not find its adjacent `codex-code-mode-host`.
  Invoking the application-bundled executable by its full path resolved that error.
- Direct model requests timed out. The machine's existing system HTTPS proxy
  worked; passing its URL through `HTTPS_PROXY`/`HTTP_PROXY` to only the acceptance
  subprocess restored connectivity. Use your actual network settings, not a
  hard-coded proxy from another machine.
- Noninteractive approval policy `never` alone did not authorize MCP writes.
  The explicit approval mode for the allow-listed fixture server was also needed.
  The rejected attempt made no memory writes.

These are setup observations from the tested CLI, not memory-store defects.
This is a guided tool-use acceptance check; it does not measure spontaneous
memory use, broad model reliability, real-world source authentication, or
canon/narrative propagation policy.
