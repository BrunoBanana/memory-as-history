"""Mechanical miss indicators over the preserved semantic baseline, not causal labels."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

from memory_as_history.benchmarks.retrieval import load_locomo, prepare_locomo
from memory_as_history.storage import _tokenize


def diagnose(data, report):
    prepared = prepare_locomo(data)
    conversations = {c['id']: c for c in prepared}
    questions = {q['id']: q for c in prepared for q in c['queries']}
    lexical = {r['question_id']: r for r in report['results'] if r['system'] == 'memory_as_history'}
    counts, details = Counter(), []
    for row in report['results']:
        if row['system'] != 'hybrid':
            continue
        query = questions[row['question_id']]
        docs = {d['id']: d for d in conversations[row['conversation']]['documents']}
        selected = set(row['selected_ids'])
        missing = sorted(set(row['gold_ids']) - selected)
        multi = len(row['gold_ids']) > 1
        counts['multi_questions'] += multi
        counts['incomplete_multi_questions'] += multi and bool(missing)
        counts['cross_session_multi_questions'] += multi and len({m.split(':')[0] for m in row['gold_ids']}) > 1
        counts['missing_evidence_turns'] += len(missing)
        indicators = []
        for mid in missing:
            distances = [abs(int(mid.split(':')[1]) - int(s.split(':')[1])) for s in selected if mid.split(':')[0] == s.split(':')[0]]
            distance = min(distances) if distances else None
            overlap = bool(set(_tokenize(query['text'])).intersection(_tokenize(docs[mid]['text'])))
            counts['missing_within_one_turn_of_selected'] += distance is not None and distance <= 1
            counts['missing_within_three_turns_of_selected'] += distance is not None and distance <= 3
            counts['missing_zero_query_token_overlap'] += not overlap
            indicators.append({'id': mid, 'nearest_selected_session_distance': distance, 'lexical_overlap': overlap})
        regression = row['recall'] < lexical[row['question_id']]['recall']
        counts['regressions'] += regression
        details.append({'question_id': row['question_id'], 'regression': regression,
                        'selected_ids': row['selected_ids'], 'missing': indicators})
    return {'counts': dict(counts), 'questions': details,
            'limits': ['Mechanical indicators can overlap; these are not verified root causes.',
                       'Neighbor opportunities ignore displacement under the fixed budget.',
                       'Semantic ambiguity, pronouns and temporal reasoning need independent annotation.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.baseline.read_bytes()
    result = diagnose(load_locomo(args.data), json.loads(gzip.decompress(raw)))
    result['baseline_gzip_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['counts']))
