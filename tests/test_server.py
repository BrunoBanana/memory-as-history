"""Protocol regressions: exercise the installed server over real MCP stdio."""

import json
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def session(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_as_history.server"],
        env={"MEMORY_AS_HISTORY_DB": str(tmp_path / "mcp.db")},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                yield client


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    # Compare the stable wire schema; MCP 2.x uses snake_case attributes.
    wire = result.model_dump(by_alias=True)
    assert not wire["isError"], wire["content"]
    return json.loads(wire["content"][0]["text"])


@pytest.mark.anyio
async def test_flag_sensitive_returns_result_and_lifts_anchor(session):
    memory = await call(session, "remember", {"content": "A claimed identity"})
    args = {"memory_id": memory["id"], "reason": "identity statement"}
    await call(session, "promote", args)
    await call(session, "pin", args)

    flagged = await call(session, "flag_sensitive", {
        "memory_id": memory["id"], "reason": "unverified source",
    })
    assert flagged["id"] == memory["id"]
    assert flagged["security_sensitive"] is True
    assert flagged["unpinned_by_sensitivity"] is True
    assert (await call(session, "recall", {}))["anchors"] == []
    denied = await call(session, "pin", args)
    assert denied["error"] == "PermissionError"
    assert "corroborate" in denied["hint"]


@pytest.mark.anyio
@pytest.mark.parametrize("name,arguments", [
    ("remember", {"content": "  "}),
    ("remember", {"content": "fact", "tier": "invalid"}),
    ("narrate", {"content": "", "reason": "new synthesis"}),
    ("narrate", {"content": "synthesis", "reason": "  "}),
])
async def test_capture_errors_are_structured_and_do_not_change_state(
    session, name, arguments,
):
    error = await call(session, name, arguments)
    assert error["error"] == "ValueError"
    assert error["message"]
    assert error["hint"]
    recalled = await call(session, "recall", {})
    assert recalled["memories"] == []
    assert recalled["narrative"] is None


@pytest.mark.anyio
async def test_provenance_gate_and_testimony_error_over_stdio(session):
    error = await call(session, "remember", {"content": "Claim", "tier": "testimony"})
    assert error["error"] == "ValueError"
    assert "corroborate" in error["hint"]
    memory = await call(session, "remember", {"content": "Claim"})
    first = await call(session, "corroborate", {"memory_id": memory["id"], "source": "one"})
    assert first["tier"] == "archive"
    support = await call(session, "provenance", {"memory_id": memory["id"]})
    assert support["corroboration_satisfied"] is False
    second = await call(session, "corroborate", {"memory_id": memory["id"], "source": "two"})
    assert second["tier"] == "testimony"
    support = await call(session, "provenance", {"memory_id": memory["id"]})
    assert support["corroboration_satisfied"] is True
    missing = await call(session, "provenance", {"memory_id": "missing"})
    assert missing["error"] == "KeyError"


@pytest.mark.anyio
async def test_unpin_optional_reason_and_legacy_response_over_stdio(session):
    tools = await session.list_tools()
    schema = next(t for t in tools.tools if t.name == "unpin").model_dump(by_alias=True)["inputSchema"]
    assert "reason" in schema["properties"]
    assert "reason" not in schema.get("required", [])
    memory = await call(session, "remember", {"content": "Preference"})
    args = {"memory_id": memory["id"], "reason": "durable"}
    await call(session, "promote", args)
    await call(session, "pin", args)
    invalid = await call(session, "unpin", {**args, "reason": " "})
    assert invalid["error"] == "ValueError"
    assert invalid["hint"]
    assert len((await call(session, "recall", {}))["anchors"]) == 1
    expected = {"memory_id": memory["id"], "unpinned": True}
    assert await call(session, "unpin", args) == expected
    assert await call(session, "unpin", {"memory_id": memory["id"]}) == expected
    assert (await call(session, "recall", {}))["anchors"] == []
