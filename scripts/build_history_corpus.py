"""Authoring source for history-v1. Freeze the expanded data before scoring."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ['anchor_noise', 'canon_rotation', 'fact_update', 'restore_review',
            'known_source', 'unknown_source', 'sensitive_synthesis', 'retroactive_flag',
            'interpretation_review', 'framed_conflict', 'version_history', 'lexical_restart']
SITUATIONS = [('cedar', '雪松'), ('maple', '枫树'), ('willow', '柳树'), ('birch', '白桦'), ('pine', '青松')]


def build_case(family, variant, language):
    en = language == 'en'
    topic = SITUATIONS[variant][0 if en else 1]
    primary = f'Project {topic} uses an amber archive.' if en else f'{topic}项目使用琥珀档案。'
    secondary = f'Project {topic} now uses a cobalt vault.' if en else f'{topic}项目现在使用钴蓝金库。'
    query = f'{topic} amber archive' if en else f'{topic}琥珀档案'
    source = f'register:{topic}'
    steps = []

    def action(op, bind=None, error=None, **args):
        item = {'op': op, 'args': args}
        if bind:
            item['bind'] = bind
        if error:
            item['expect_error'] = error
        steps.append(item)

    def assertion(path, op, value, metric):
        return {'path': path, 'op': op, 'value': value, 'metric': metric}

    def check(read, *checks, **args):
        steps.append({'op': 'reopen'})
        steps.append({'op': 'check', 'read': read, 'args': args, 'assertions': list(checks)})

    def audit(*actions):
        check('audit_log', *(assertion('*.action', 'contains', a, 'audit_coverage') for a in actions), limit=1000)

    def priority():
        action('promote', memory_id='$a', reason='durable project evidence')

    def story(bind='story', content=primary, ids=None, **kwargs):
        action('narrate', bind=bind, content=content, reason='fixture synthesis',
               memory_ids=ids or ['$a'], **kwargs)

    def unavailable(metric='stale_exposure'):
        check('recall', assertion('narrative', 'equal', None, metric),
              assertion('narrative_review.id', 'equal', '$story', 'narrative_review'))

    def noise():
        steps.append({'op': 'noise', 'count': 7 + variant * 3,
                      'text': 'Unrelated appointment at the cafe' if en else '咖啡馆的临时约会记录'})

    args = {'content': primary, 'source': source, 'frame': 'team-a'}
    if family in ('known_source', 'unknown_source'):
        args['security_sensitive'] = True
    if family == 'unknown_source':
        args['source'] = [None, '', '   ', None, '\t'][variant]
    if family == 'interpretation_review':
        args['tier'] = 'interpretation'
    action('remember', bind='a', **args)
    check('get', assertion('content', 'equal', primary, 'history_preservation'), memory_id='$a')

    if family == 'anchor_noise':
        priority(); action('pin', memory_id='$a', reason='lasting project context'); noise()
        check('recall', assertion('anchors.*.id', 'contains', '$a', 'retention'), limit=2, query='appointment')
        action('unpin', memory_id='$a', reason='identity role ended')
        check('recall', assertion('anchors', 'equal', [], 'lifecycle'),
              assertion('memories.*.id', 'contains', '$a', 'retrieval'), query=query, limit=2)
        audit('promote', 'pin', 'unpin')
    elif family == 'canon_rotation':
        priority(); action('canonize', memory_id='$a', scope='release', reason='current task'); noise()
        check('recall', assertion('canon.*.memory_id', 'contains', '$a', 'retention'), limit=2)
        action('end_scope', scope='release', reason='task finished')
        check('recall', assertion('canon', 'equal', [], 'lifecycle'),
              assertion('memories.*.id', 'contains', '$a', 'retrieval'), query=query, limit=2)
        audit('canonize', 'end_scope')
    elif family == 'fact_update':
        story(); action('forget', memory_id='$a', reason='old plan withdrawn')
        action('remember', bind='b', content=secondary, source=source)
        check('recall', assertion('memories.*.id', 'excludes', '$a', 'stale_exposure'),
              assertion('memories.*.id', 'contains', '$b', 'retention'),
              assertion('narrative', 'equal', None, 'stale_exposure'))
        story('replacement', secondary, ['$b'])
        check('recall', assertion('narrative.id', 'equal', '$replacement', 'retention'))
        audit('forget', 'narrative_invalidated', 'narrate')
    elif family == 'restore_review':
        story(); action('forget', memory_id='$a', reason='evidence withdrawn'); unavailable()
        action('restore', memory_id='$a', reason='evidence reinstated'); unavailable()
        action('review_narrative', narrative_id='$story', note='account checked against restored evidence')
        check('recall', assertion('narrative.id', 'equal', '$story', 'retention'),
              assertion('narrative_review', 'equal', None, 'narrative_review'))
        audit('forget', 'restore', 'narrative_invalidated', 'review_narrative')
    elif family == 'known_source':
        priority()
        action('pin', memory_id='$a', reason='unverified claim', error='PermissionError')
        for repeated in (source, f' {source} '):
            action('corroborate', memory_id='$a', source=repeated)
        check('provenance', assertion('independent_corroboration_count', 'equal', 0, 'source_count'), memory_id='$a')
        action('pin', memory_id='$a', reason='repetition is not independence', error='PermissionError')
        action('corroborate', memory_id='$a', source='independent:inspection')
        action('pin', memory_id='$a', reason='independent evidence obtained')
        check('recall', assertion('anchors.*.id', 'contains', '$a', 'retention'))
        audit('pin_denied', 'corroborate', 'corroborate_upgrade', 'pin')
    elif family == 'unknown_source':
        priority()
        for label in ('register:first', 'register:first'):
            action('corroborate', memory_id='$a', source=label)
        action('canonize', memory_id='$a', scope='release', reason='unverified', error='PermissionError')
        check('provenance', assertion('independent_corroboration_count', 'equal', 0, 'source_count'), memory_id='$a')
        action('corroborate', memory_id='$a', source='register:second')
        action('canonize', memory_id='$a', scope='release', reason='two sources obtained')
        check('recall', assertion('canon.*.memory_id', 'contains', '$a', 'retention'))
        audit('canonize_denied', 'corroborate_upgrade', 'canonize')
    elif family == 'sensitive_synthesis':
        action('narrate', content=primary, reason='unsupported synthesis', security_sensitive=True, error='PermissionError')
        action('narrate', content=primary, reason='single-source synthesis', memory_ids=['$a'], security_sensitive=True, error='PermissionError')
        check('current_narrative', assertion('', 'equal', None, 'unsupported_priority'))
        action('corroborate', memory_id='$a', source='independent:inspection')
        story(security_sensitive=True)
        check('recall', assertion('narrative.id', 'equal', '$story', 'retention'),
              assertion('narrative.security_sensitive', 'equal', True, 'lifecycle'))
        audit('narrate_denied', 'narrate')
    elif family == 'retroactive_flag':
        priority(); action('pin', memory_id='$a', reason='core fact')
        for scope in ('release', 'handover'):
            action('canonize', memory_id='$a', scope=scope, reason='active project')
        story(); action('flag_sensitive', memory_id='$a', reason='unverified authority identified')
        check('recall', assertion('anchors', 'equal', [], 'unsupported_priority'),
              assertion('canon', 'equal', [], 'unsupported_priority'),
              assertion('narrative', 'equal', None, 'stale_exposure'),
              assertion('memories.*.id', 'contains', '$a', 'retention'))
        action('corroborate', memory_id='$a', source='independent:inspection'); unavailable()
        action('review_narrative', narrative_id='$story', note='account checked after source verification')
        check('recall', assertion('narrative.id', 'equal', '$story', 'retention'))
        audit('unpin_by_sensitivity', 'decanonize_by_sensitivity', 'narrative_invalidated')
    elif family == 'interpretation_review':
        story(); action('due_for_review', days=0); unavailable()
        action('review', memory_id='$a', note='inference rechecked'); unavailable()
        action('review_narrative', narrative_id='$story', note='synthesis also rechecked')
        check('recall', assertion('narrative.id', 'equal', '$story', 'retention'))
        audit('narrative_invalidated', 'review', 'review_narrative')
    elif family == 'framed_conflict':
        action('remember', bind='b', content=secondary, source='team-b:register', frame='team-b')
        action('mark_conflict', bind='conflict', memory_id_a='$a', memory_id_b='$b', reason='different team requirements')
        check('recall', assertion('memories.*.id', 'contains', '$a', 'retention'),
              assertion('memories.*.id', 'excludes', '$b', 'frame_isolation'),
              assertion('conflicts.*.id', 'contains', '$conflict', 'conflict_visibility'), frame='team-a')
        action('forget', memory_id='$b', reason='team-b requirement withdrawn')
        check('recall', assertion('conflicts', 'equal', [], 'stale_exposure'),
              assertion('memories.*.id', 'excludes', '$b', 'stale_exposure'))
        action('restore', memory_id='$b', reason='historical requirement active again')
        action('resolve_conflict', conflict_id='$conflict', reason='adopt team-a for joint work', adopted_memory_id='$a')
        check('recall', assertion('conflicts', 'equal', [], 'conflict_visibility'),
              assertion('memories.*.id', 'contains', '$b', 'history_preservation'))
        audit('mark_conflict', 'resolve_conflict')
    elif family == 'version_history':
        story(); action('remember', bind='b', content=secondary, source=source)
        story('replacement', secondary, ['$b'])
        action('forget', memory_id='$a', reason='old evidence withdrawn')
        check('recall', assertion('narrative.id', 'equal', '$replacement', 'retention'))
        action('review_narrative', narrative_id='$story', note='cannot approve superseded text', error='ValueError')
        check('narrative_history', assertion('*.content', 'contains', primary, 'history_preservation'),
              assertion('*.content', 'contains', secondary, 'history_preservation'),
              assertion('1.superseded_by', 'equal', '$replacement', 'history_preservation'))
        audit('narrate', 'forget')
    else:
        noise()
        check('recall', assertion('memories.0.id', 'equal', '$a', 'retrieval'), query=query, limit=3)
        action('forget', memory_id='$a', reason='project retired')
        check('recall', assertion('memories.*.id', 'excludes', '$a', 'stale_exposure'), query=query, limit=3)
        check('get', assertion('content', 'equal', primary, 'history_preservation'), memory_id='$a')
        audit('forget')
    return {'id': f'{family}-{variant}-{language}', 'family': family,
            'variant': variant, 'language': language,
            'split': 'dev' if variant < 3 else 'test', 'steps': steps}


def main():
    cases = [build_case(f, v, lang) for f in FAMILIES for v in range(5) for lang in ('en', 'zh')]
    corpus = {'schema_version': 1, 'name': 'history-v1', 'cases': cases}
    path = ROOT/'src/memory_as_history/benchmarks/data/history-v1.json'
    # One case per line keeps each complete episode reviewable without macro expansion.
    encoded = '{"schema_version": 1, "name": "history-v1", "cases": [\n' + ',\n'.join(
        json.dumps(case, ensure_ascii=False) for case in cases) + '\n]}\n'
    path.write_text(encoded, encoding='utf-8')
    manifest = {'name': 'history-v1', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'episodes': 120, 'families': 12, 'language_counts': {'en': 60, 'zh': 60},
                'split_counts': {'dev': 72, 'test': 48}, 'license': 'MIT',
                'provenance': 'Synthetic, author-visible; 12 templates, 5 situations with paired translations. Not independent user histories.',
                'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    path.with_name('history-v1.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
