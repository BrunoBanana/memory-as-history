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


@pytest.mark.anyio
async def test_search_schema_empty_store_and_input_error_over_stdio(session):
    tools = await session.list_tools()
    search = next((t for t in tools.tools if t.name == 'search'), None)
    assert search is not None
    schema = search.model_dump(by_alias=True)['inputSchema']
    assert schema['required'] == ['query']
    assert {'mode', 'limit', 'frame'} <= schema['properties'].keys()
    result = await call(session, 'search', {'query': 'question'})
    assert result['memories'] == []
    assert result['retrieval']['inference_performed'] is False
    error = await call(session, 'search', {'query': 'question', 'mode': 'invalid'})
    assert error['error'] == 'ValueError' and error['hint']


@pytest.mark.anyio
@pytest.mark.parametrize('literal', ['123456789012', '1e999', 'null', 'false', '[]', '{"x":1}'])
async def test_string_arguments_remain_literal_over_stdio(session, literal):
    memory = await call(session, 'remember', {'content': literal, 'source': literal, 'frame': literal})
    assert memory['content'] == memory['source'] == memory['frame'] == literal
    recalled = await call(session, 'recall', {'query': literal, 'frame': literal})
    assert recalled['memories'][0]['id'] == memory['id']


@pytest.mark.anyio
@pytest.mark.parametrize('memory_id', ['123456789012', '1e9999999999'])
async def test_legacy_numeric_or_exponent_ids_survive_stdio(session, tmp_path, monkeypatch, memory_id):
    from memory_as_history import storage
    # Existing databases can already contain these valid twelve-hex-digit IDs.
    with monkeypatch.context() as patch:
        patch.setattr(storage, '_new_id', lambda: memory_id)
        store = storage.Store(tmp_path / 'mcp.db')
        try:
            store.remember('legacy ID fixture')
        finally:
            store.close()
    forgotten = await call(session, 'forget', {'memory_id': memory_id, 'reason': 'withdraw legacy record'})
    assert forgotten['id'] == memory_id


@pytest.mark.anyio
async def test_json_encoded_list_keeps_legacy_structured_argument_support(session):
    memory = await call(session, 'remember', {'content': 'fixture evidence'})
    narrative = await call(session, 'narrate', {'content': 'fixture narrative', 'reason': 'synthesis',
                                              'memory_ids': json.dumps([memory['id']])})
    assert narrative['memory_ids'] == [memory['id']]


@pytest.mark.anyio
async def test_history_tools_chronology_and_retractable_links_over_stdio(session):
    listed = await session.list_tools()
    assert {'search_history','timeline','set_history_context','link_memories','unlink_memories','memory_links'} <= {t.name for t in listed.tools}
    earlier = await call(session, 'remember', {'content':'original date', 'event_at':'2024-01-01T08:00:00+08:00', 'session_id':'null', 'session_position':0})
    later = await call(session, 'remember', {'content':'deadline changed', 'event_at':'2024-02-01T00:00:00Z', 'session_id':'null', 'session_position':1})
    assert earlier['event_at'] == '2024-01-01T00:00:00+00:00' and earlier['session_id']=='null'
    edge = await call(session,'link_memories',{'from_id':later['id'],'to_id':earlier['id'],'relation':'updates','reason':'revised notice'})
    rows = await call(session,'timeline',{'session_id':'null'})
    assert [r['id'] for r in rows['memories']] == [earlier['id'],later['id']]
    result = await call(session,'search_history',{'query':'deadline','mode':'lexical','limit':5})
    assert len(result['memories'])==2
    await call(session,'unlink_memories',{'link_id':edge['id'],'reason':'mistaken association'})
    links=await call(session,'memory_links',{'memory_id':later['id'],'include_retired':True})
    if isinstance(links,dict): links=links.get('result', [links])
    assert links[0]['retired_at']
    changed=await call(session,'set_history_context',{'memory_id':later['id'],'reason':'clear uncertain date'})
    assert changed['event_at'] is None and changed['session_id'] is None
    error=await call(session,'search_history',{'query':'deadline','since':'2024-01-01','mode':'lexical'})
    assert error['error']=='ValueError' and error['hint']
