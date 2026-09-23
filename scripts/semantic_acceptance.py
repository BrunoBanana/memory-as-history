"""Explicit cached-model acceptance over two real MCP server processes.

Run after installing the semantic extra and downloading its pinned model.
This script uses synthetic data in a temporary database, never a user's store.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(client, name, arguments):
    wire = (await client.call_tool(name, arguments)).model_dump(by_alias=True)
    assert not wire['isError'], wire
    value = json.loads(wire['content'][0]['text'])
    assert not isinstance(value, dict) or 'error' not in value, value
    return value


async def verify(root):
    params = StdioServerParameters(command=sys.executable,
                                  args=['-m', 'memory_as_history.server'],
                                  env={'MEMORY_AS_HISTORY_DB': str(root / 'memory.db'),
                                       'MEMORY_AS_HISTORY_TOOLS': 'full',
                                       'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    checks = []
    with anyio.fail_after(120):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                memory = await call(client, 'remember', {'content': 'Mira requires dairy-free meals on flights.', 'frame': 'travel'})
                await call(client, 'remember', {'content': 'The milk carton has a blue label.', 'frame': 'travel'})
                for query, mode in [('What dietary restriction applies to Mira when flying?', 'hybrid'),
                                    ('米拉坐飞机时不能吃什么？', 'semantic')]:
                    result = await call(client, 'search', {'query': query, 'mode': mode, 'frame': 'travel', 'limit': 1})
                    assert result['memories'][0]['id'] == memory['id'], result
                    assert result['retrieval']['inference_performed'] is True
                    checks.append({'check': f'{mode}_paraphrase', 'passed': True})
                await call(client, 'forget', {'memory_id': memory['id'], 'reason': 'synthetic acceptance withdrawal'})
                result = await call(client, 'search', {'query': 'Mira flight meal', 'mode': 'hybrid', 'limit': 5})
                assert memory['id'] not in [row['id'] for row in result['memories']]
                checks.append({'check': 'withdrawal_after_cached_ranking', 'passed': True})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                result = await call(client, 'search', {'query': 'Mira flight meal', 'mode': 'semantic', 'limit': 5})
                assert memory['id'] not in [row['id'] for row in result['memories']]
                assert len(result['memories']) == 1
                checks.append({'check': 'withdrawal_after_server_restart', 'passed': True})
    return {'passed': True, 'server_processes': 2, 'search_calls': 4,
            'checks': checks, 'backend': result['retrieval']['backend']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='history-semantic-acceptance-') as directory:
        report = anyio.run(verify, Path(directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
