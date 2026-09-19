"""Exercise the knowledge contract over real MCP stdio, including restarts."""
import json

import pytest

from test_sessions import connect


@pytest.fixture
def anyio_backend():
    return 'asyncio'


async def invoke(client, tool, **arguments):
    wire = (await client.call_tool(tool, arguments)).model_dump(by_alias=True)
    assert not wire['isError'], wire
    values = [json.loads(block['text']) for block in wire['content'] if block['type'] == 'text']
    if tool in ('list_narratives', 'narrative_history'):
        if len(values) == 1 and isinstance(values[0], dict) and 'result' in values[0]:
            return values[0]['result']
        return values[0] if len(values) == 1 and isinstance(values[0], list) else values
    assert len(values) == 1
    return values[0]


@pytest.mark.anyio
async def test_knowledge_discovery_and_literal_metadata_over_stdio(tmp_path):
    async with connect(tmp_path / 'protocol.db') as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert {'create_claim','add_evidence','retract_evidence','adopt_claim','revise_claim',
                'withdraw_claim','inspect_claim','recall_claims','search_archive','list_narratives'} <= tools.keys()
        schema = tools['narrate'].model_dump(by_alias=True)['inputSchema']
        assert {'scope','perspective','coverage','claim_ids','link_ids'} <= schema['properties'].keys()
        for literal in ('null', '1e999', '{"x":1}'):
            memory = await invoke(client, 'remember', content=literal, material_type='utterance',
                                  origin_id=literal, capture_context=literal)
            assert memory['origin_id'] == memory['capture_context'] == literal
            claim = await invoke(client, 'create_claim', content=literal, kind='self_report', reason='capture',
                                 scope=literal, asserted_by=literal)
            assert claim['content'] == claim['scope'] == claim['asserted_by'] == literal
            evidence = await invoke(client, 'add_evidence', claim_id=claim['id'], memory_id=memory['id'],
                                    stance='supports', reason='exact words', quote=literal, locator=literal)
            assert evidence['quote'] == evidence['locator'] == literal
            await invoke(client, 'adopt_claim', claim_id=claim['id'], reason='use as self-report')
            narrative = await invoke(client, 'narrate', content='Attributed self-report', reason='synthesis',
                                     scope=literal, perspective=literal, coverage=literal,
                                     claim_ids=json.dumps([claim['id']]))
            assert narrative['scope'] == narrative['perspective'] == narrative['coverage'] == literal
            assert (await invoke(client, 'current_narrative', scope=literal))['id'] == narrative['id']


@pytest.mark.anyio
async def test_revision_history_forgetting_and_scopes_survive_three_processes(tmp_path):
    path = tmp_path / 'restarts.db'
    async with connect(path) as client:
        source = await invoke(client, 'remember', content='June plan', origin_id='notice-1')
        old = await invoke(client, 'create_claim', content='June plan', kind='plan', reason='capture')
        await invoke(client, 'add_evidence', claim_id=old['id'], memory_id=source['id'], stance='supports', reason='notice')
        adopted = await invoke(client, 'adopt_claim', claim_id=old['id'], reason='initial plan')
        checkpoint = adopted['events'][-1]['recorded_at']
        await invoke(client, 'narrate', content='June plan', reason='synthesis', claim_ids=[old['id']])
        await invoke(client, 'narrate', content='Separate operations account', reason='different criterion', scope='operations')

    async with connect(path) as client:
        source2 = await invoke(client, 'remember', content='July plan', event_at='2020-01-01T00:00:00Z', origin_id='notice-2')
        new = await invoke(client, 'create_claim', content='July plan', kind='plan', reason='late notice',
                           statement_at='2020-01-01T00:00:00Z')
        await invoke(client, 'add_evidence', claim_id=new['id'], memory_id=source2['id'], stance='supports', reason='notice')
        revised = await invoke(client, 'revise_claim', claim_id=old['id'], replacement_id=new['id'], reason='delay')
        assert revised['status'] == 'adopted'
        past = await invoke(client, 'recall_claims', as_of=checkpoint)
        assert [c['id'] for c in past['claims']] == [old['id']]
        current = await invoke(client, 'recall_claims')
        assert [c['id'] for c in current['claims']] == [new['id']]
        assert (await invoke(client, 'recall'))['narrative'] is None
        assert (await invoke(client, 'current_narrative', scope='operations'))['review_status'] == 'current'

    async with connect(path) as client:
        assert (await invoke(client, 'inspect_claim', claim_id=old['id']))['replacement_id'] == new['id']
        assert len(await invoke(client, 'list_narratives')) == 2
        await invoke(client, 'forget', memory_id=source['id'], reason='stop recall of old wording')
        inspected = await invoke(client, 'inspect_claim', claim_id=old['id'], as_of=checkpoint)
        assert inspected['redacted'] and 'June plan' not in json.dumps(inspected)
        await invoke(client, 'withdraw_claim', claim_id=new['id'], reason='plan rescinded')
        assert (await invoke(client, 'recall_claims'))['claims'] == []


@pytest.mark.anyio
async def test_relationship_dependencies_and_archive_are_exposed_over_stdio(tmp_path):
    async with connect(tmp_path / 'relationships.db') as client:
        a = await invoke(client, 'remember', content='Migration report')
        b = await invoke(client, 'remember', content='Outage report', frame='operations', capture_context='Incident notes only')
        edge = await invoke(client, 'link_memories', from_id=a['id'], to_id=b['id'], relation='explains', reason='hypothesis')
        narrative = await invoke(client, 'narrate', content='Possible cause', reason='provisional', link_ids=[edge['id']], scope='cause')
        await invoke(client, 'unlink_memories', link_id=edge['id'], reason='unsupported')
        assert (await invoke(client, 'current_narrative', scope='cause'))['review_status'] == 'stale'
        assert (await invoke(client, 'narrative_history', scope='cause'))[0]['id'] == narrative['id']
        archive = await invoke(client, 'search_archive', query='Outage', frame='operations', limit=1)
        assert archive['memories'][0]['id'] == b['id']
        assert archive['retrieval']['priority_policy'] == 'no_reserved_slots'
        claim = await invoke(client, 'create_claim', content='Outage recorded', kind='observation', reason='capture')
        evidence = await invoke(client, 'add_evidence', claim_id=claim['id'], memory_id=b['id'], stance='supports', reason='report')
        await invoke(client, 'adopt_claim', claim_id=claim['id'], reason='adopt')
        await invoke(client, 'retract_evidence', evidence_id=evidence['id'], reason='citation requires review')
        assert (await invoke(client, 'recall_claims'))['claims'] == []


@pytest.mark.anyio
@pytest.mark.parametrize('tool,args', [
    ('create_claim', {'content':'x','kind':'verified','reason':'capture'}),
    ('add_evidence', {'claim_id':'missing','memory_id':'missing','stance':'supports','reason':'check'}),
    ('recall_claims', {'as_of':'yesterday'}),
    ('search_archive', {'query':' '}),
    ('narrate', {'content':'x','reason':'y','scope':' '}),
    ('current_narrative', {'scope':' '}),
])
async def test_new_tool_errors_are_structured(tmp_path, tool, args):
    async with connect(tmp_path / 'errors.db') as client:
        error = await invoke(client, tool, **args)
        assert error['error'] in ('ValueError', 'KeyError')
        assert error['message'] and error['hint']
