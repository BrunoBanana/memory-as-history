#!/usr/bin/env python3
"""Executable synthetic mechanism and bilingual lexical regression evaluation.

The recency-only baseline is deliberately simple; it does not represent Mem0,
RAG, or any other product. This fixture measures specified storage/ranking
behavior, not real-world agent quality or industry-wide superiority.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid

from memory_as_history.storage import Store


class NaiveStore:
    """Synthetic recency-only baseline: recall the last N inserted rows."""

    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute('CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def remember(self, content):
        mid = uuid.uuid4().hex[:12]
        self.conn.execute('INSERT INTO memories VALUES (?, ?)', (mid, content))
        self.conn.commit()
        return mid

    def recall(self, limit=5):
        rows = self.conn.execute(
            'SELECT id, content FROM memories ORDER BY rowid DESC LIMIT ?', (limit,),
        ).fetchall()
        return [{'id': row[0], 'content': row[1]} for row in rows]


IDENTITY = "IDENTITY: fixture user Alice works in data engineering."


def run_naive_baseline(n_noise, recall_limit):
    with tempfile.TemporaryDirectory() as directory:
        with closing(NaiveStore(Path(directory) / 'naive.db')) as store:
            identity = store.remember(IDENTITY)
            for i in range(n_noise):
                store.remember(f'Unrelated fixture observation {i}')
            result = store.recall(limit=recall_limit)
            return any(row['id'] == identity for row in result), result


def run_memory_as_history(n_noise, recall_limit):
    with tempfile.TemporaryDirectory() as directory:
        with closing(Store(Path(directory) / 'history.db')) as store:
            memory = store.remember(IDENTITY, source='fixture declaration')
            store.promote(memory.id, reason='foundational fixture identity')
            store.pin(memory.id, reason='core fixture identity')
            for i in range(n_noise):
                store.remember(f'Unrelated fixture observation {i}')
            result = store.recall(limit=recall_limit)
            return any(row['id'] == memory.id for row in result['anchors']), result


def evaluate():
    failures = []
    retention = []
    for noise in (3, 10, 50, 200):
        limit = 5
        baseline_found, _ = run_naive_baseline(noise, limit)
        anchor_found, _ = run_memory_as_history(noise, limit)
        passed = anchor_found and baseline_found == (noise < limit)
        retention.append({'noise': noise, 'limit': limit, 'baseline_found': baseline_found,
                          'anchor_found': anchor_found, 'passed': passed})
        if not passed:
            failures.append(f'anchor retention contract failed with {noise} noise rows')

    fixture_path = Path(__file__).resolve().parent / 'evaluations' / 'retrieval.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    rankings = []
    with tempfile.TemporaryDirectory() as directory:
        with closing(Store(Path(directory) / 'retrieval.db')) as store:
            ids = {store.remember(doc['content']).id: doc['id'] for doc in fixture['documents']}
            for query in fixture['queries']:
                result = store.recall(query=query['query'], limit=len(ids))['memories']
                ordered = [ids[row['id']] for row in result]
                rank = ordered.index(query['target']) + 1 if query['target'] in ordered else None
                passed = rank == 1
                rankings.append({**query, 'rank': rank, 'top_id': ordered[0] if ordered else None,
                                 'passed': passed})
                if not passed:
                    failures.append(f"lexical regression: {query['query']!r}, expected {query['target']!r} at rank 1, got {rank}")
    count = len(rankings)
    return {
        'schema_version': 1,
        'scope': 'Synthetic mechanism and hand-authored bilingual lexical regression; not a competitor or real-world agent benchmark.',
        'passed': not failures,
        'anchor_retention': {'cases': retention, 'pass_rate': sum(row['passed'] for row in retention) / len(retention)},
        'lexical_retrieval': {
            'corpus_size': len(fixture['documents']), 'query_count': count,
            'hit_at_1': sum(row['passed'] for row in rankings) / count,
            'mrr': sum(1 / row['rank'] if row['rank'] else 0 for row in rankings) / count,
            'cases': rankings,
        },
        'failures': failures,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='emit machine-readable metrics')
    args = parser.parse_args(argv)
    report = evaluate()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report['scope'])
        for case in report['anchor_retention']['cases']:
            print(f"Noise={case['noise']}: recency baseline={case['baseline_found']}, anchor={case['anchor_found']}")
        lexical = report['lexical_retrieval']
        print(f"Lexical fixture: {lexical['query_count']} queries / {lexical['corpus_size']} documents; "
              f"Hit@1={lexical['hit_at_1']:.3f}, MRR={lexical['mrr']:.3f}")
        for failure in report['failures']:
            print(f'FAIL: {failure}')
        print('PASS' if report['passed'] else 'FAIL')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
