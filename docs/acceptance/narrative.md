# Narrative withdrawal and review acceptance

Verified on 2026-09-18 (Asia/Shanghai): three fresh Codex conversations, 15
completed MCP calls, and an independently checked read-only SQLite snapshot.
The server used the narrative implementation in `208b36c`; the subsequent
retrieval correction and documentation changes do not alter this scenario.
Environment: macOS, Python 3.12.13, MCP 2.2.0; Codex CLI
0.155.0-alpha.2.6, GPT-6 Astra, xhigh reasoning.

| Fresh conversation | Tool-response assertions |
| --- | --- |
| 1 | Capture fictional project fact; link a narrative; recall it; forget the source; narrative text disappears from recall while a source issue remains |
| 2 | Discover IDs through tools alone; inspect retained text; premature review returns ValueError; restore the source; narrative stays unavailable |
| 3 | Discover pending review despite active evidence; explicitly review; original ID/text returns; inspect persisted auditing |

The snapshot contained one active memory, one original narrative, and five audit
rows in order: `narrate`, `narrative_invalidated`, `forget`, `restore`,
`review_narrative`. SQLite `integrity_check` returned `ok`. The rejected review
created no successful review audit. All tool responses, not just final model
claims, were compared against these assertions and the snapshot.
[Selected synthetic evidence](narrative-evidence.json) includes tool arguments,
responses, snapshot and hashes of the local raw event files. No real user memory
or prior transcript was passed into later conversations.

## Reproduction

The credential-free regression runs in CI:

```sh
python -m pytest tests/test_sessions.py -q
```

For the actual client, install the project in `.venv` and configure an accessible
model and working CLI path. The [anchor acceptance guide](cross-session.md)
explains the tested CLI, network and fixture-only MCP permission setup. Use the
machine's existing proxy if required; no global client configuration is edited.
This invokes a guided test client, not an autonomous implementation agent.

```sh
project_dir="$(pwd)"
trial_dir="$(mktemp -d)"
mkdir -p "$trial_dir/client"
server_python="$project_dir/.venv/bin/python"
# Set ACCEPTANCE_CODEX_BIN and ACCEPTANCE_MODEL for your installation.
for phase in 1 2 3; do
  case "$phase" in
    1) phase_tools='"remember","narrate","forget"' ;;
    2) phase_tools='"restore","review_narrative"' ;;
    3) phase_tools='"review_narrative"' ;;
  esac
  mcp_override="mcp_servers.memory_acceptance={command=\"$server_python\",args=[\"-m\",\"memory_as_history.server\"],env={MEMORY_AS_HISTORY_DB=\"$trial_dir/fixture.db\"},required=true,default_tools_approval_mode=\"approve\",enabled_tools=[\"recall\",\"current_narrative\",\"audit_log\",$phase_tools]}"
  "$ACCEPTANCE_CODEX_BIN" --ask-for-approval never \
    --disable multi_agent --disable shell_tool --disable apps --disable plugins \
    --disable browser_use --disable computer_use \
    exec --ephemeral --ignore-user-config --skip-git-repo-check \
    --sandbox read-only --model "$ACCEPTANCE_MODEL" \
    -c 'model_reasoning_effort="xhigh"' -c 'web_search="disabled"' \
    -c "$mcp_override" --cd "$trial_dir/client" --json \
    --output-last-message "$trial_dir/phase$phase-final.json" - \
    < "$project_dir/docs/acceptance/prompts/narrative-phase$phase.txt" \
    > "$trial_dir/phase$phase.jsonl" 2> "$trial_dir/phase$phase.stderr" || break
  # Inspect tool responses and require status="passed" before proceeding.
done
```

Require the three rows of assertions above and compare actual MCP results with
`memories`, `narratives`, and `audit_log` via a read-only SQLite connection. List
results may be emitted as multiple text blocks; parse every block. An exit code
of zero or the model's own `passed` report is insufficient.

This is one guided synthetic scenario. It does not establish spontaneous memory
use, semantic entailment, authenticated provenance, or reliability across models.
The broader evidence requirements are in [evaluation scope](../evaluation.md).
