"""Freeze authored challenge histories before history-search scoring."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src/memory_as_history/benchmarks/data'
TOPICS = [('falcon', 'Falcon release', '猎鹰版本', 'dev'),
          ('harbor', 'Harbor shipment', '港湾发货', 'dev'),
          ('cedar', 'Cedar exhibition', '雪松展览', 'dev'),
          ('lumen', 'Lumen workshop', '流明工作坊', 'dev'),
          ('orbit', 'Orbit rehearsal', '轨道彩排', 'test'),
          ('willow', 'Willow inspection', '柳树检查', 'test'),
          ('coral', 'Coral catalog', '珊瑚目录', 'test'),
          ('summit', 'Summit registration', '山峰报名', 'test')]


def build():
    cases = []
    for code, en, zh, split in TOPICS:
        for lang, topic in [('en', en), ('zh', zh)]:
            for family in ('linked', 'session', 'temporal'):
                cid = f'{code}-{lang}-{family}'
                texts = ([f'{topic}: the deadline changed to April 10 following the checklist review.',
                          'The checklist found a missing signature; it was returned for correction.',
                          'Version R1 specified March 4 as the original deadline.'] if lang == 'en' else
                         [f'{topic}：检查清单复核后，截止日期改成四月十日。',
                          '清单发现缺少签名，材料已退回补正。',
                          '第一版 R1 规定原截止日期为三月四日。'])
                docs = []
                for i, text in enumerate(texts):
                    docs.append({'id': f'e{i}', 'content': text, 'source': f'fixture:{cid}',
                                 'event_at': f'2025-03-{10+i:02d}T09:00:00Z',
                                 'session_id': f'{cid}:s1' if i < 2 else f'{cid}:s0',
                                 'session_position': i if i < 2 else 0})
                for i in range(12):
                    text = (f'{topic} deadline planning note {i}: discussion of a possible change; no approved change recorded.' if lang == 'en' else
                            f'{topic}截止日期计划备忘 {i}：讨论可能的调整，尚未记录获批的变更。')
                    docs.append({'id': f'n{i}', 'content': text, 'source': f'fixture:{cid}:noise',
                                 'event_at': None if i % 3 == 0 else f'2025-04-{i+1:02d}T09:00:00Z',
                                 'session_id': f'{cid}:noise:{i}', 'session_position': 0})
                query = (f'{topic}: what changed about the deadline and what was the reason?' if lang == 'en' else
                         f'{topic}的截止日期发生了什么变更，原因是什么？')
                gold = ['e0', 'e1', 'e2'] if family == 'linked' else ['e0', 'e1']
                links = ([{'from_id': 'e0', 'to_id': 'e2', 'relation': 'updates', 'reason': 'new notice explicitly revises R1'},
                          {'from_id': 'e0', 'to_id': 'e1', 'relation': 'explains', 'reason': 'notice references checklist'}]
                         if family == 'linked' else [])
                options = {'since': '2025-03-01T00:00:00Z', 'until': '2025-03-31T23:59:59Z'} if family == 'temporal' else {}
                cases.append({'id': cid, 'split': split, 'language': lang, 'family': family,
                              'documents': docs, 'links': links, 'query': query,
                              'query_options': options, 'gold': gold})
    return {'schema_version': 1, 'cases': cases,
            'limits': ['Author-visible templates and paired translations; not a blind independent holdout.',
                       'Test topics are disjoint from dev topics; no test/LoCoMo tuning.',
                       'Explicit fixture links/time bounds are supplied; no automatic relation/date understanding claim.']}


if __name__ == '__main__':
    raw = (json.dumps(build(), ensure_ascii=False, indent=2) + '\n').encode()
    (ROOT / 'history-retrieval-v1.json').write_bytes(raw)
    manifest = {'file': 'history-retrieval-v1.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                'cases': 48, 'dev_cases': 24, 'test_cases': 24, 'license': 'MIT',
                'policy': 'Freeze before scoring. Test split never used for parameter selection; author-visible, not blind.'}
    (ROOT / 'history-retrieval-v1.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(manifest)
