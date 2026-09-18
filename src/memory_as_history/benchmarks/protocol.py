"""Run frozen longitudinal state contracts through the public Store API."""
import hashlib
import json
from pathlib import Path
import tempfile
import time

from memory_as_history.storage import Store
from .common import environment

DATA = Path(__file__).parent / 'data'
WRITES = {'remember', 'promote', 'pin', 'unpin', 'canonize', 'end_scope', 'forget',
          'restore', 'corroborate', 'narrate', 'review_narrative', 'flag_sensitive',
          'review', 'due_for_review', 'mark_conflict', 'resolve_conflict'}
READS = {'recall', 'get', 'provenance', 'audit_log', 'narrative_history', 'current_narrative'}
MISSING = object()


def _references(value):
    if isinstance(value, str) and value.startswith('$'):
        yield value[1:]
    elif isinstance(value, dict):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def validate_corpus(corpus):
    if corpus.get('schema_version') != 1 or not corpus.get('cases'):
        raise ValueError('nonempty schema_version=1 corpus required')
    seen = set()
    for case in corpus['cases']:
        if not isinstance(case.get('id'), str) or not case['id'] or case['id'] in seen:
            raise ValueError('episode IDs must be nonempty and unique')
        seen.add(case['id'])
        if case.get('split') not in ('dev', 'test') or case.get('language') not in ('en', 'zh'):
            raise ValueError('invalid split/language')
        bound = set()
        checks = 0
        for step in case.get('steps', []):
            op = step.get('op')
            if op not in WRITES | {'check', 'reopen', 'noise'}:
                raise ValueError(f'unsupported operation: {op}')
            if not set(_references(step)) <= bound:
                raise ValueError('unknown or forward fixture reference')
            if step.get('expect_error') not in (None, 'ValueError', 'PermissionError', 'KeyError'):
                raise ValueError('invalid expected exception')
            if op == 'check':
                if step.get('read') not in READS or not step.get('assertions'):
                    raise ValueError('check needs an allowed read and assertions')
                for assertion in step['assertions']:
                    if (assertion.get('op') not in ('equal', 'contains', 'excludes')
                            or not isinstance(assertion.get('path'), str)
                            or not assertion.get('metric') or 'value' not in assertion):
                        raise ValueError('invalid assertion')
                checks += len(step['assertions'])
            if op == 'noise' and (type(step.get('count')) is not int or not 1 <= step['count'] <= 1000):
                raise ValueError('invalid noise count')
            if 'bind' in step:
                if op not in {'remember', 'narrate', 'mark_conflict'} or step.get('expect_error'):
                    raise ValueError('only successful creations may bind IDs')
                if not isinstance(step['bind'], str) or step['bind'] in bound:
                    raise ValueError('invalid or duplicate binding')
                bound.add(step['bind'])
        if not checks or sum(s['op'] == 'reopen' for s in case['steps']) < 2:
            raise ValueError('episode needs assertions and at least three connection lifetimes')


def load_corpus():
    raw = (DATA / 'history-v1.json').read_bytes()
    manifest = json.loads((DATA / 'history-v1.manifest.json').read_text())
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('frozen corpus SHA-256 mismatch')
    corpus = json.loads(raw)
    validate_corpus(corpus)
    return corpus


def _path(value, parts):
    if not parts:
        return value
    head, *tail = parts
    if head == '*' and isinstance(value, list):
        values = [_path(item, tail) for item in value]
        return MISSING if any(v is MISSING for v in values) else values
    if isinstance(value, dict) and head in value:
        return _path(value[head], tail)
    if isinstance(value, list) and head.isdecimal() and int(head) < len(value):
        return _path(value[int(head)], tail)
    return MISSING


def score_check(response, assertion):
    actual = _path(response, assertion['path'].split('.') if assertion['path'] else [])
    expected = assertion['value']
    passed = False
    if actual is not MISSING:
        if assertion['op'] == 'equal':
            passed = actual == expected
        elif isinstance(actual, (list, str)):
            passed = expected in actual
            if assertion['op'] == 'excludes':
                passed = not passed
    return {**assertion, 'passed': bool(passed),
            'actual': {'missing': True} if actual is MISSING else actual}


def _resolve(value, bindings):
    if isinstance(value, str) and value.startswith('$'):
        return bindings[value[1:]]
    if isinstance(value, list):
        return [_resolve(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, bindings) for k, v in value.items()}
    return value


def _normalize(value, bindings):
    reverse = {v: '$' + k for k, v in bindings.items()}
    if isinstance(value, str):
        return reverse.get(value, value)
    if isinstance(value, list):
        return [_normalize(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v, bindings) for k, v in value.items()}
    return value


def run_case(case, directory):
    path = Path(directory) / 'history.db'
    store = Store(path)
    bindings, checks, errors = {}, [], []
    lifetimes = 1
    started = time.perf_counter()
    try:
        for index, step in enumerate(case['steps']):
            op = step['op']
            try:
                if op == 'reopen':
                    store.close()
                    store = Store(path)
                    lifetimes += 1
                elif op == 'noise':
                    for i in range(step['count']):
                        bindings[f'noise-{index}-{i}'] = store.remember(f"{step['text']} {i}").id
                elif op == 'check':
                    response = getattr(store, step['read'])(**_resolve(step.get('args', {}), bindings))
                    if hasattr(response, 'to_dict'):
                        response = response.to_dict()
                    response = _normalize(response, bindings)
                    checks.extend({'step': index, **score_check(response, assertion)}
                                  for assertion in step['assertions'])
                else:
                    try:
                        result = getattr(store, op)(**_resolve(step.get('args', {}), bindings))
                    except (ValueError, PermissionError, KeyError) as exc:
                        if not step.get('expect_error'):
                            raise
                        checks.append({'step': index, 'metric': 'guard',
                                       'passed': type(exc).__name__ == step['expect_error'],
                                       'expected': step['expect_error'], 'actual': type(exc).__name__})
                    else:
                        if step.get('expect_error'):
                            checks.append({'step': index, 'metric': 'guard', 'passed': False,
                                           'expected': step['expect_error'], 'actual': 'accepted'})
                        if 'bind' in step:
                            bindings[step['bind']] = result.id if hasattr(result, 'id') else result['id']
            except Exception as exc:
                errors.append({'step': index, 'op': op, 'error': type(exc).__name__, 'message': str(exc)})
                # Keep the predeclared denominator: blocked checks are failures,
                # separately labeled so they are not mistaken for observations.
                for remaining_index in range(index, len(case['steps'])):
                    remaining = case['steps'][remaining_index]
                    pending = list(remaining.get('assertions', []))
                    if remaining.get('expect_error'):
                        pending.append({'metric': 'guard', 'expected': remaining['expect_error']})
                    completed = sum(check['step'] == remaining_index for check in checks)
                    checks.extend({'step': remaining_index, **assertion, 'passed': False,
                                   'blocked': True, 'actual': {'blocked_by_step': index}}
                                  for assertion in pending[completed:])
                break
    finally:
        store.close()
    return {k: case[k] for k in ('id', 'family', 'language', 'split')} | {
        'passed': bool(checks) and not errors and all(c['passed'] for c in checks),
        'connection_lifetimes': lifetimes, 'checks': checks, 'errors': errors,
        'elapsed_ms': (time.perf_counter() - started) * 1000, 'database_bytes': path.stat().st_size}


def run_protocol(cases=None):
    cases = load_corpus()['cases'] if cases is None else cases
    validate_corpus({'schema_version': 1, 'cases': cases})
    results = []
    with tempfile.TemporaryDirectory(prefix='history-benchmark-') as root:
        for index, case in enumerate(cases):
            directory = Path(root) / str(index)
            directory.mkdir()
            results.append(run_case(case, directory))
    metrics = {}
    for row in results:
        for check in row['checks']:
            bucket = metrics.setdefault(check['metric'], {'passed': 0, 'failed': 0, 'blocked': 0, 'total': 0})
            bucket['passed' if check['passed'] else 'failed'] += 1
            bucket['total'] += 1
            bucket['blocked'] += int(check.get('blocked', False))
    for bucket in metrics.values():
        bucket['pass_rate'] = bucket['passed'] / bucket['total']
    groups = {}
    for dimension in ('family', 'language', 'split'):
        groups[dimension] = {key: {'episodes': sum(r[dimension] == key for r in results),
                                   'passed': sum(r[dimension] == key and r['passed'] for r in results)}
                             for key in sorted({r[dimension] for r in results})}
    return {'schema_version': 1, 'track': 'synthetic_protocol', 'environment': environment(),
            'corpus_sha256': hashlib.sha256((DATA / 'history-v1.json').read_bytes()).hexdigest(),
            'selected_cases_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'episodes': len(results), 'passed': all(r['passed'] for r in results),
            'failed_episodes': sum(not r['passed'] for r in results),
            'metrics': metrics, 'groups': groups, 'results': results,
            'limits': ['12 authored templates; variants/translations are correlated',
                       'Author-visible dev/test splits; not independent held-out histories',
                       'Explicit public API actions; not autonomous tool choice or source authentication']}
