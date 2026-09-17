"""Run deterministic history contracts or pinned external evidence retrieval."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from .protocol import load_corpus, run_protocol
from .retrieval import MANIFEST, load_locomo, run_retrieval


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='track', required=True)
    protocol = commands.add_parser('protocol')
    protocol.add_argument('--split', choices=('all', 'dev', 'test'), default='all')
    external = commands.add_parser('locomo')
    external.add_argument('--data', type=Path, required=True)
    external.add_argument('--max-items', type=int, default=5)
    external.add_argument('--max-bytes', type=int, default=4096)
    for command in (protocol, external):
        command.add_argument('--output', type=Path, help='write JSON report (otherwise stdout)')
    args = parser.parse_args(argv)
    try:
        if args.track == 'protocol':
            cases = load_corpus()['cases']
            if args.split != 'all':
                cases = [c for c in cases if c['split'] == args.split]
            report = run_protocol(cases)
            exit_code = 0 if report['passed'] else 1
        else:
            report = run_retrieval(load_locomo(args.data), args.max_items, args.max_bytes)
            report['dataset'] = MANIFEST
            report['dataset_sha256'] = hashlib.sha256(args.data.read_bytes()).hexdigest()
            exit_code = 0  # Measured quality is not a preselected pass threshold.
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding='utf-8')
            print(json.dumps({'track': report.get('track'), 'output': str(args.output), 'exit_code': exit_code}))
        else:
            print(encoded, end='')
        return exit_code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Benchmark error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
