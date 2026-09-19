"""Authored chronological/link challenge with explicit, author-visible metadata."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import tempfile
import time

from memory_as_history.storage import Store
from .common import environment
from .retrieval import apply_budget, score_retrieval

DATA = Path(__file__).parent / 'data'
MANIFEST = json.loads((DATA / 'history-retrieval-v1.manifest.json').read_text())


def load_challenge():
    raw = (DATA / MANIFEST['file']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST['sha256']:
        raise ValueError('history challenge SHA-256 mismatch')
    return json.loads(raw)


def _summary(rows):
    latency = sorted(r['query_ms'] for r in rows)
    return {'queries': len(rows), 'failures': sum('error' in r for r in rows),
            **{key: statistics.mean(r[key] for r in rows) for key in ('recall', 'hit', 'mrr')},
            'complete_evidence_rate': statistics.mean(r['recall'] == 1 for r in rows),
            'query_ms_p50': statistics.median(latency),
            'query_ms_p95': latency[math.ceil(.95 * len(rows))-1]}


def run_challenge(cases, semantic_backend=None, max_items=5, max_bytes=4096):
    apply_budget([], max_items, max_bytes)
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('nonempty unique history cases required')
    modes = ('lexical', 'hybrid') if semantic_backend is not None else ('lexical',)
    configs = [(mode + suffix, mode, expand) for mode in modes for suffix, expand in
               (('', None), ('_time', 'none'), ('_links', 'links'), ('_session', 'session'), ('_both', 'both'))]
    results = []
    for case in cases:
        docs = case['documents']
        lookup = {d['id']: d for d in docs}
        if not case['gold'] or not set(case['gold']) <= lookup.keys() or len(lookup) != len(docs):
            raise ValueError('unique documents and nonempty resolved gold required')
        with tempfile.TemporaryDirectory(prefix='history-challenge-') as root:
            path = Path(root) / 'memory.db'
            store = Store(path, semantic_backend=semantic_backend)
            try:
                mapping = {d['id']: store.remember(**{k:v for k,v in d.items() if k != 'id'}).id for d in docs}
                for link in case['links']:
                    store.link_memories(mapping[link['from_id']], mapping[link['to_id']], link['relation'], link['reason'])
                inverse = {v:k for k,v in mapping.items()}
                store.close()
                store = Store(path, semantic_backend=semantic_backend)
                if semantic_backend is not None:
                    semantic_backend.prepare_documents([d['content'] for d in docs])
                for system, mode, expand in configs:
                    started = time.perf_counter()
                    row = {'system': system, 'case_id': case['id'], 'family': case['family'],
                           'language': case['language'], 'split': case['split'], 'gold_ids': case['gold']}
                    try:
                        if expand is None:
                            result = (store.recall(case['query'], limit=max_items) if mode == 'lexical' else
                                      store.search(case['query'], limit=max_items, mode=mode))
                        else:
                            result = store.search_history(case['query'], limit=max_items, mode=mode,
                                                          expand=expand, **case['query_options'])
                        selected = apply_budget([{'id':inverse[r['id']], 'text':r['content']} for r in result['memories']], max_items, max_bytes)
                        row['selected_ids'] = [d['id'] for d in selected]
                        row['content_bytes'] = sum(len(d['text'].encode('utf-8')) for d in selected)
                        row.update(score_retrieval(row['selected_ids'], case['gold']))
                    except Exception as exc:
                        row.update(selected_ids=[], content_bytes=0, recall=0, hit=0, mrr=0,
                                   error=f'{type(exc).__name__}: {exc}')
                    row['query_ms'] = (time.perf_counter()-started)*1000
                    results.append(row)
            finally:
                store.close()
    systems = {}
    for name, _, _ in configs:
        rows = [r for r in results if r['system'] == name]
        systems[name] = {**_summary(rows), 'by_family': {
            family: _summary([r for r in rows if r['family']==family]) for family in sorted({r['family'] for r in rows})}}
    return {'schema_version':1, 'track':'authored_history_retrieval', 'environment':environment(),
            'completed': not any('error' in r for r in results), 'cases':len(cases),
            'selected_cases_sha256':hashlib.sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'dataset':MANIFEST, 'budget':{'max_items':max_items,'max_utf8_content_bytes':max_bytes},
            'systems':systems, 'results':results,
            'semantic_backend':semantic_backend.describe() if semantic_backend is not None else None,
            'limits':['Author-visible authored histories and explicit links/ranges; not blind independent evaluation.',
                      'Base systems ignore structured time bounds; *_time isolates the effect of filtering.',
                      'All challenge systems take an item-limited prefix then skip oversized items without refilling.',
                      'Model/document loading excluded from query timings; later systems share bounded caches.']}
