"""Text evidence retrieval over pinned external data; not official QA scoring."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import tempfile
import time

from memory_as_history.storage import Store, _tokenize
from .common import environment

MANIFEST = json.loads((Path(__file__).parent / 'data/locomo.manifest.json').read_text())
DEVELOPMENT_MANIFEST = json.loads((Path(__file__).parent / 'data/semantic-dev-v1.manifest.json').read_text())
SYSTEMS = ('memory_as_history', 'reference_bm25', 'recency')


def load_locomo(path, expected_sha256=MANIFEST['sha256']):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('external dataset SHA-256 mismatch')
    data = json.loads(raw)
    prepare_locomo(data)  # Fail on invalid data before starting any indexing.
    return data


def load_development():
    return load_locomo(Path(__file__).parent / 'data/semantic-dev-v1.json', DEVELOPMENT_MANIFEST['sha256'])


def prepare_locomo(data, history=False):
    if not isinstance(data, list) or not data:
        raise ValueError('nonempty conversation list required')
    prepared, seen = [], set()
    for item in data:
        cid = str(item.get('sample_id', ''))
        if not cid or cid in seen:
            raise ValueError('unique sample_id required')
        seen.add(cid)
        conversation = item.get('conversation', {})
        sessions = sorted((int(m.group(1)), key) for key in conversation
                          if (m := re.fullmatch(r'session_(\d+)', key)))
        documents, ids = [], set()
        for _, key in sessions:
            if not isinstance(conversation[key], list):
                raise ValueError('session must be a list of turns')
            for position, turn in enumerate(conversation[key]):
                mid = turn.get('dia_id')
                if not isinstance(mid, str) or not mid or mid in ids:
                    raise ValueError('unique dialogue IDs required within each conversation')
                if not isinstance(turn.get('text'), str) or not isinstance(turn.get('speaker'), str):
                    raise ValueError('turn text and speaker must be strings')
                text = f"{turn['speaker']}: {turn['text']}"
                # Caption text is data supplied by upstream, never fetched media.
                caption = turn.get('blip_caption')
                if isinstance(caption, str) and caption:
                    text += '\nImage caption: ' + caption
                doc = {'id': mid, 'text': text}
                if history:
                    doc.update(session_id=f'{cid}:{key}', session_position=position)
                documents.append(doc)
                ids.add(mid)
        if not documents or not isinstance(item.get('qa'), list) or not item['qa']:
            raise ValueError('conversation needs turns and questions')
        queries = []
        for index, question in enumerate(item['qa']):
            if (not isinstance(question.get('question'), str) or not question['question'].strip()
                    or type(question.get('category')) is not int or question['category'] not in range(1, 6)):
                raise ValueError('invalid question/category')
            evidence = question.get('evidence', [])
            if not isinstance(evidence, list) or any(not isinstance(x, str) for x in evidence):
                raise ValueError('evidence must be a list of dialogue IDs')
            gold = list(dict.fromkeys(x.strip() for x in evidence))
            excluded = ('adversarial' if question['category'] == 5 else
                        'no_evidence' if not gold else
                        'unresolved_evidence' if not set(gold) <= ids else None)
            queries.append({'id': f'{cid}:{index}', 'text': question['question'],
                            'category': question['category'], 'gold': gold, 'excluded': excluded})
        prepared.append({'id': cid, 'documents': documents, 'queries': queries})
    return prepared


def apply_budget(ranked, max_items, max_bytes):
    """Greedily select whole items in rank order; skip items that do not fit.

    Only UTF-8 content bytes are budgeted, not metadata or model tokenizer units.
    Oversized items remain relevant gold and can therefore cause genuine misses.
    """
    if type(max_items) is not int or type(max_bytes) is not int or min(max_items, max_bytes) < 1:
        raise ValueError('budgets must be positive integers')
    selected, consumed = [], 0
    for doc in ranked:
        size = len(doc['text'].encode('utf-8'))
        if consumed + size <= max_bytes:
            selected.append(doc)
            consumed += size
            if len(selected) == max_items:
                break
    return selected


def score_retrieval(selected_ids, gold_ids):
    gold = set(gold_ids)
    if not gold:
        raise ValueError('retrieval scores require nonempty evidence')
    relevant = set(selected_ids) & gold
    reciprocal = next((1 / rank for rank, mid in enumerate(selected_ids, 1) if mid in gold), 0)
    return {'recall': len(relevant) / len(gold), 'hit': int(bool(relevant)), 'mrr': reciprocal}


class ReferenceBM25:
    """Independent BM25 equation with the product tokenizer held constant.

    Cache document frequencies/lengths before querying. This is an in-memory
    reference ranker, not a complete competing memory product.
    """
    def __init__(self, documents):
        self.documents = documents
        tokens = [_tokenize(doc['text']) for doc in documents]
        self.counts = [Counter(row) for row in tokens]
        self.lengths = [len(row) for row in tokens]
        self.average = sum(self.lengths) / len(tokens) if tokens else 0
        self.frequency = Counter(token for row in self.counts for token in row)

    def rank(self, query):
        terms = set(_tokenize(query))
        scores = []
        for index, (counts, length) in enumerate(zip(self.counts, self.lengths)):
            score = 0.0
            for term in sorted(terms):
                occurrences = counts[term]
                if not occurrences:
                    continue
                df = self.frequency[term]
                idf = math.log(1 + (len(self.documents) - df + .5) / (df + .5))
                norm = .25 + .75 * length / (self.average or 1)
                score += idf * occurrences * 2.5 / (occurrences + 1.5 * norm)
            scores.append((score, index))
        # Both systems use latest inserted document to resolve score ties.
        return [self.documents[index] for _, index in sorted(scores, reverse=True)]


def reference_bm25(documents, query):
    return ReferenceBM25(documents).rank(query)


def _aggregate(rows):
    if not rows:
        return {'queries': 0, 'recall': None, 'hit': None, 'mrr': None}
    return {'queries': len(rows), **{metric: statistics.mean(r[metric] for r in rows)
                                   for metric in ('recall', 'hit', 'mrr')}}


def _diagnostics(prepared, max_items, max_bytes):
    rows = []
    for conversation in prepared:
        lookup = {doc["id"]: doc for doc in conversation["documents"]}
        for query in conversation["queries"]:
            if query["excluded"]:
                continue
            evidence = [lookup[mid] for mid in query["gold"]]
            terms = set(_tokenize(query["text"]))
            # Gold labels are used only here, after all systems have ranked.
            # Selecting shortest evidence gives a content-budget upper bound,
            # not another system's measured retrieval score.
            feasible = apply_budget(sorted(evidence, key=lambda doc: len(doc["text"].encode("utf-8"))),
                                    max_items, max_bytes)
            rows.append({"question_id": query["id"],
                         "gold_exceeds_item_budget": len(evidence) > max_items,
                         "gold_exceeds_byte_budget": sum(len(doc["text"].encode("utf-8")) for doc in evidence) > max_bytes,
                         "budget_recall_ceiling": len(feasible) / len(evidence),
                         "gold_without_query_token_overlap": sum(not terms.intersection(_tokenize(doc["text"])) for doc in evidence)})
    return {"gold_exceeds_item_budget": sum(row["gold_exceeds_item_budget"] for row in rows),
            "gold_exceeds_byte_budget": sum(row["gold_exceeds_byte_budget"] for row in rows),
            "mean_budget_recall_ceiling": statistics.mean(row["budget_recall_ceiling"] for row in rows),
            "gold_without_query_token_overlap": sum(row["gold_without_query_token_overlap"] for row in rows),
            "per_question": rows}


def run_retrieval(data, max_items=5, max_bytes=4096, semantic_backend=None, history=False):
    apply_budget([], max_items, max_bytes)  # Validate even if nothing is eligible.
    prepared = prepare_locomo(data, history=history)
    results, exclusions, index_stats = [], Counter(), []
    system_names = SYSTEMS + (('semantic', 'hybrid') if semantic_backend is not None else ())
    if history:
        system_names += ('history_lexical',) + (('history_hybrid',) if semantic_backend is not None else ())
    excluded_questions = []
    for conversation in prepared:
        docs = conversation['documents']
        with tempfile.TemporaryDirectory(prefix='history-external-') as root:
            path = Path(root) / 'memory.db'
            store = Store(path, semantic_backend=semantic_backend)
            try:
                started = time.perf_counter()
                mapping = {store.remember(d['text'], source=f"dialogue:{d['id']}",
                           **({'session_id': d['session_id'], 'session_position': d['session_position']} if history else {})).id: d['id'] for d in docs}
                product_index_ms = (time.perf_counter() - started) * 1000
                # Reopen the persisted index before any question is supplied.
                store.close()
                store = Store(path, semantic_backend=semantic_backend)
                started = time.perf_counter()
                reference = ReferenceBM25(docs)
                reference_index_ms = (time.perf_counter() - started) * 1000
                lookup = {d['id']: d for d in docs}
                started = time.perf_counter()
                if semantic_backend is not None:
                    semantic_backend.prepare_documents([doc['text'] for doc in docs])
                semantic_preparation_ms = (time.perf_counter() - started) * 1000
                index_stats.append({'conversation': conversation['id'], 'documents': len(docs),
                                    'database_bytes': path.stat().st_size,
                                    'product_index_ms': product_index_ms,
                                    'reference_index_ms': reference_index_ms,
                                    'semantic_preparation_ms': semantic_preparation_ms if semantic_backend is not None else None})
                for query in conversation['queries']:
                    if query['excluded']:
                        exclusions[query['excluded']] += 1
                        excluded_questions.append({'question_id': query['id'], 'reason': query['excluded']})
                        continue
                    for system in system_names:
                        started = time.perf_counter()
                        if system == 'memory_as_history':
                            recall = store.recall(query=query['text'], limit=len(docs))
                            ranked = [lookup[mapping[row['id']]] for row in recall['memories']]
                        elif system == 'reference_bm25':
                            ranked = reference.rank(query['text'])
                        elif system in ('history_lexical', 'history_hybrid'):
                            recall = store.search_history(query['text'], limit=max_items, mode=system.removeprefix('history_'), expand='session')
                            ranked = [lookup[mapping[row['id']]] for row in recall['memories']]
                        elif system in ('semantic', 'hybrid'):
                            recall = store.search(query=query['text'], limit=len(docs), mode=system)
                            ranked = [lookup[mapping[row['id']]] for row in recall['memories']]
                        else:
                            ranked = list(reversed(docs))
                        selected = apply_budget(ranked, max_items, max_bytes)
                        elapsed = (time.perf_counter() - started) * 1000
                        selected_ids = [row['id'] for row in selected]
                        results.append({'system': system, 'conversation': conversation['id'],
                                        'question_id': query['id'], 'category': query['category'],
                                        'selected_ids': selected_ids, 'gold_ids': query['gold'],
                                        'content_bytes': sum(len(row['text'].encode('utf-8')) for row in selected),
                                        'query_ms': elapsed, **score_retrieval(selected_ids, query['gold'])})
            finally:
                store.close()
    systems = {}
    for system in system_names:
        rows = [r for r in results if r['system'] == system]
        latencies = sorted(r['query_ms'] for r in rows)
        systems[system] = {**_aggregate(rows),
                           'query_ms_p50': statistics.median(latencies) if rows else None,
                           'query_ms_p95': latencies[math.ceil(.95 * len(rows)) - 1] if rows else None,
                           'mean_content_bytes': statistics.mean(r['content_bytes'] for r in rows) if rows else None,
                           'by_evidence_count': {
                               name: {**_aggregate(group),
                                      'complete_evidence_rate': statistics.mean(r['recall'] == 1 for r in group) if group else None}
                               for name, group in (('single', [r for r in rows if len(r['gold_ids']) == 1]),
                                                   ('multiple', [r for r in rows if len(r['gold_ids']) > 1]))},
                           'by_category': {str(c): _aggregate([r for r in rows if r['category'] == c]) for c in range(1, 5)},
                           'by_conversation': {c['id']: _aggregate([r for r in rows if r['conversation'] == c['id']]) for c in prepared}}
    product = {r['question_id']: r for r in results if r['system'] == 'memory_as_history'}
    paired = {}
    for baseline in SYSTEMS[1:]:
        counts = Counter()
        for row in results:
            if row['system'] == baseline:
                delta = product[row['question_id']]['recall'] - row['recall']
                counts['win' if delta > 0 else 'loss' if delta < 0 else 'tie'] += 1
        paired[baseline] = {outcome: counts[outcome] for outcome in ('win', 'tie', 'loss')}
    semantic_pairs = {}
    for system in system_names[3:]:
        counts = Counter()
        for row in results:
            if row['system'] == system:
                delta = row['recall'] - product[row['question_id']]['recall']
                counts['win' if delta > 0 else 'loss' if delta < 0 else 'tie'] += 1
        semantic_pairs[system] = {key: counts[key] for key in ('win', 'tie', 'loss')}
    total = sum(len(c['queries']) for c in prepared)
    scored = len(product)
    if not scored:
        raise ValueError('no scorable questions; evaluation would have zero coverage')
    return {'schema_version': 1, 'track': 'external_evidence_retrieval', 'environment': environment(),
            'completed': True, 'total_questions': total, 'scored_questions': scored,
            'exclusions': dict(exclusions), 'excluded_questions': excluded_questions,
            'budget': {'max_items': max_items, 'max_utf8_content_bytes': max_bytes},
            'history_expansion': history,
            'diagnostics': _diagnostics(prepared, max_items, max_bytes),
            'systems': systems, 'paired_recall_outcomes': paired, 'index_stats': index_stats, 'results': results,
            'semantic_backend': semantic_backend.describe() if semantic_backend is not None else None,
            'semantic_paired_against_lexical': semantic_pairs,
            'limits': ['Evidence-turn retrieval only; not official LoCoMo QA accuracy or abstention',
                       'Shared tokenization; independent BM25 equation; semantic modes are opt-in',
                       'Semantic document preparation is separate; hybrid follows semantic and shares its bounded query cache',
                       'Questions within each conversation are correlated; no iid confidence interval',
                       'Product latency includes SQLite; reference rankers are in memory',
                       'Whole-item UTF-8 content budget excludes metadata and is not a model-token budget',
                       'History variants use session adjacency only, no oracle links/event times; oversized prefix items are not refilled']}
