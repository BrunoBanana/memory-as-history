"""Two-process MCP acceptance for chronology and links; optional real local encoder."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(client,name,args):
    wire=(await client.call_tool(name,args)).model_dump(by_alias=True)
    assert not wire['isError'],wire
    values=[json.loads(c['text']) for c in wire['content'] if c['type']=='text']
    result=values[0] if len(values)==1 else values
    assert not isinstance(result,dict) or 'error' not in result,result
    return result


async def verify(root, semantic):
    params=StdioServerParameters(command=sys.executable,args=['-m','memory_as_history.server'],
        env={'MEMORY_AS_HISTORY_DB':str(root/'history.db'),'MEMORY_AS_HISTORY_TOOLS':'full','HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'})
    mode='hybrid' if semantic else 'lexical'
    checks=[]
    with anyio.fail_after(120):
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as client:
                await client.initialize()
                prior=await call(client,'remember',{'content':'Comet previous deadline was March 4.', 'event_at':'2025-03-01T00:00:00Z','session_id':'before','session_position':0,'frame':'comet'})
                seed=await call(client,'remember',{'content':'Comet deadline changed to April 10 after review.', 'event_at':'2025-03-10T00:00:00Z','session_id':'after','session_position':0,'frame':'comet'})
                reason=await call(client,'remember',{'content':'A missing review signature delayed the Comet schedule.', 'event_at':'2025-03-09T00:00:00Z','session_id':'after','session_position':1,'frame':'comet'})
                await call(client,'remember',{'content':'Office coffee supply is now restocked.','frame':'comet'})
                await call(client,'remember',{'content':'Comet unrelated private record','frame':'other'})
                await call(client,'link_memories',{'from_id':seed['id'],'to_id':prior['id'],'relation':'updates','reason':'notice revises original schedule'})
                edge=await call(client,'link_memories',{'from_id':reason['id'],'to_id':seed['id'],'relation':'explains','reason':'review gives the reason'})
                await call(client,'narrate',{'content':'Comet changed deadline after missing signature.', 'reason':'reviewed records','memory_ids':[prior['id'],seed['id'],reason['id']]})
                for query in ('Comet deadline history and reason','彗星项目的截止日期为什么改变？'):
                    result=await call(client,'search_history',{'query':query,'frame':'comet','mode':mode,'limit':5})
                    assert {prior['id'],seed['id'],reason['id']} <= {r['id'] for r in result['memories']}
                    assert result['retrieval']['inference_performed'] is semantic
                    checks.append({'check':'history_evidence_'+('en' if query.startswith('Comet') else 'zh'),'passed':True})
                timeline=await call(client,'timeline',{'frame':'comet','since':'2025-03-01T00:00:00Z','until':'2025-03-31T23:59:59Z'})
                assert [r['id'] for r in timeline['memories']]==[prior['id'],reason['id'],seed['id']]
                checks.append({'check':'explicit_event_order_and_unknown_time_exclusion','passed':True})
                await call(client,'unlink_memories',{'link_id':edge['id'],'reason':'withdraw unsupported explanation link'})
                result=await call(client,'search_history',{'query':'Comet','frame':'comet','mode':mode,'limit':5})
                assert all(p.get('link_id')!=edge['id'] for p in result['retrieval']['evidence_paths'])
                checks.append({'check':'retired_link_not_traversed','passed':True})
                await call(client,'forget',{'memory_id':prior['id'],'reason':'withdraw old notice'})
                result=await call(client,'search_history',{'query':'Comet','frame':'comet','mode':mode,'limit':5})
                assert prior['id'] not in {r['id'] for r in result['memories']} and result['narrative'] is None
                checks.append({'check':'withdrawal_and_narrative_review','passed':True})
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as client:
                await client.initialize()
                result=await call(client,'search_history',{'query':'Comet','frame':'comet','mode':mode,'limit':5})
                assert prior['id'] not in {r['id'] for r in result['memories']} and result['narrative'] is None
                checks.append({'check':'restart_preserves_withdrawal','passed':True})
    return {'passed':True,'server_processes':2,'search_calls':5,'mode':mode,'checks':checks,'backend':result['retrieval']['backend']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--semantic',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='history-mcp-acceptance-') as root:
        report=anyio.run(verify,Path(root),args.semantic)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'output':str(args.output)}))
