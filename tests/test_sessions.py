"""Complete MCP lifecycle across fresh client/server processes, without an LLM."""

from contextlib import asynccontextmanager
import json
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def connect(db_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_as_history.server"],
        env={"MEMORY_AS_HISTORY_DB": str(db_path)},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                yield client


async def call(client, tool, **arguments):
    response = await client.call_tool(tool, arguments)
    wire = response.model_dump(by_alias=True)
    assert not wire["isError"], wire["content"]
    values = [json.loads(item["text"]) for item in wire["content"]
              if item["type"] == "text"]
    # FastMCP 1.x emits one text block per list item; MCP 2.x wraps lists.
    if tool == "audit_log":
        if len(values) == 1 and isinstance(values[0], dict) and "result" in values[0]:
            return values[0]["result"]
        if len(values) == 1 and isinstance(values[0], list):
            return values[0]
        return values
    assert len(values) == 1
    return values[0]


@pytest.mark.anyio
@pytest.mark.parametrize("origin", ["fixture:declaration", None], ids=["known", "unknown"])
async def test_sensitive_anchor_lifecycle_survives_client_and_server_restarts(tmp_path, origin):
    db_path = tmp_path / "sessions.db"
    content = "Fixture identity: ORION-7429"
    first_source = origin or "fixture:declaration"

    async with connect(db_path) as client:
        memory = await call(client, "remember", content=content, source=origin,
                            security_sensitive=True)
        mid = memory["id"]
        await call(client, "promote", memory_id=mid, reason="fixture identity")
        denied = await call(client, "pin", memory_id=mid, reason="unverified fixture")
        assert denied["error"] == "PermissionError"
        for source in (first_source, f" {first_source} "):
            observed = await call(client, "corroborate", memory_id=mid, source=source)
            assert observed["tier"] == "archive"
        denied = await call(client, "pin", memory_id=mid, reason="repeated same source")
        assert denied["error"] == "PermissionError"
        corroborated = await call(client, "corroborate", memory_id=mid,
                                  source="fixture:independent-register")
        assert corroborated["tier"] == "testimony"
        await call(client, "pin", memory_id=mid, reason="independent fixture support")

    # The first context has terminated both the client and its server process.
    async with connect(db_path) as client:
        recalled = await call(client, "recall", limit=1)
        assert len(recalled["anchors"]) == 1
        discovered = recalled["anchors"][0]
        assert discovered["id"] == mid
        assert discovered["content"] == content
        assert discovered["status"] == "consolidated"
        assert discovered["tier"] == "testimony"
        support = await call(client, "provenance", memory_id=discovered["id"])
        assert support["corroboration_satisfied"] is True
        assert support["independent_corroboration_count"] == 1
        assert support["origin_known"] is (origin is not None)
        result = await call(client, "unpin", memory_id=discovered["id"],
                            reason="fixture identity retired")
        assert result == {"memory_id": mid, "unpinned": True}

    async with connect(db_path) as client:
        recalled = await call(client, "recall", limit=1)
        assert recalled["anchors"] == []
        assert len(recalled["memories"]) == 1
        assert recalled["memories"][0]["id"] == mid
        assert recalled["memories"][0]["content"] == content
        logs = await call(client, "audit_log", limit=100)
        memory_logs = [row for row in logs if row["memory_id"] == mid]
        removals = [row for row in memory_logs if row["action"] == "unpin"]
        assert len(removals) == 1
        assert removals[0]["reason"] == "fixture identity retired"
        assert removals[0]["at"]
        assert sum(row["action"] == "pin_denied" for row in memory_logs) == 2
        assert sum(row["action"] == "corroborate" for row in memory_logs) == 3
        assert sum(row["action"] == "corroborate_upgrade" for row in memory_logs) == 1
        assert sum(row["action"] == "pin" for row in memory_logs) == 1
        assert not any(row["action"] == "unpin_noop" for row in memory_logs)
        await call(client, "unpin", memory_id=mid)  # Legacy call remains idempotent.

    async with connect(db_path) as client:
        logs = await call(client, "audit_log", limit=100)
        noops = [row for row in logs if row["memory_id"] == mid
                 and row["action"] == "unpin_noop"]
        assert len(noops) == 1
        assert noops[0]["reason"] == "legacy unpin: caller did not provide a reason"
        assert (await call(client, "recall"))["anchors"] == []


@pytest.mark.anyio
async def test_narrative_withdrawal_and_review_survive_three_server_restarts(tmp_path):
    db_path = tmp_path / 'narrative.db'
    async with connect(db_path) as client:
        tools = await client.list_tools()
        assert any(t.name == 'review_narrative' for t in tools.tools)
        memory = await call(client, 'remember', content='Fixture affiliation', source='origin')
        mid = memory['id']
        narrative = await call(client, 'narrate', content='A fixture account', reason='synthesis', memory_ids=[mid])
        nid = narrative['id']
        await call(client, 'forget', memory_id=mid, reason='affiliation withdrawn')

    async with connect(db_path) as client:
        recalled = await call(client, 'recall')
        assert recalled['narrative'] is None
        assert recalled['narrative_review']['id'] == nid
        assert 'A fixture account' not in json.dumps(recalled)
        inspected = await call(client, 'current_narrative')
        assert inspected['content'] == 'A fixture account'
        denied = await call(client, 'review_narrative', narrative_id=nid, note='premature')
        assert denied['error'] == 'ValueError'
        assert denied['hint']
        await call(client, 'restore', memory_id=mid, reason='affiliation revalidated')

    async with connect(db_path) as client:
        assert (await call(client, 'recall'))['narrative'] is None
        reviewed = await call(client, 'review_narrative', narrative_id=nid, note='account checked against restored evidence')
        assert reviewed['review_status'] == 'current'
        await call(client, 'flag_sensitive', memory_id=mid, reason='identity claim')
        assert (await call(client, 'recall'))['narrative'] is None
        await call(client, 'promote', memory_id=mid, reason='durable')
        denied = await call(client, 'canonize', memory_id=mid, scope='fixture', reason='active')
        assert denied['error'] == 'PermissionError'
        denied = await call(client, 'narrate', content='Sensitive synthesis', reason='replacement',
                            memory_ids=[mid], security_sensitive=True)
        assert denied['error'] == 'PermissionError'
        await call(client, 'corroborate', memory_id=mid, source='independent register')
        await call(client, 'review_narrative', narrative_id=nid, note='new evidence checked')

    async with connect(db_path) as client:
        assert (await call(client, 'recall'))['narrative']['id'] == nid
        logs = await call(client, 'audit_log', limit=100)
        assert sum(r['action'] == 'narrative_invalidated' for r in logs) == 2
        assert sum(r['action'] == 'review_narrative' for r in logs) == 2
        assert any(r['action'] == 'canonize_denied' for r in logs)
        assert any(r['action'] == 'narrate_denied' for r in logs)
