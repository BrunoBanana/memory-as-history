"""Run an offline, disposable example: python examples/historical_claims.py.

No database outside a temporary directory is touched. This illustrates the
protocol with authored fixtures; it is not a model-quality evaluation.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from memory_as_history.storage import Store


def demonstrate(store):
    original = store.remember('项目原计划六月上线。', source='发布计划', material_type='document',
                              origin_id='release-plan-1', event_at='2024-04-01T00:00:00Z',
                              capture_context='仅录入发布计划与延期通知，未收集会议全过程。')
    old = store.create_claim('项目计划六月上线。', 'plan', '从发布计划提取', scope='release',
                             asserted_by='项目组', statement_at='2024-04-01T00:00:00Z')
    store.add_evidence(old['id'], original.id, 'supports', '计划原文', quote='六月上线')
    adopted = store.adopt_claim(old['id'], '作为当前计划使用，不等同于已经上线')
    checkpoint = adopted['events'][-1]['recorded_at']
    store.narrate('项目预计六月上线。', '当前进度说明', claim_ids=[old['id']],
                  scope='release/product', perspective='产品团队：计划日期', coverage='已录入的发布文件')
    store.narrate('仍需观察上线后的可靠性。', '另一项评价标准', memory_ids=[original.id],
                  scope='release/operations', perspective='运维团队：服务可靠性',
                  coverage='尚无上线后的观测数据')

    notice = store.remember('四月十日通知：项目改为七月上线。', source='延期通知',
                            material_type='document', origin_id='delay-notice-1',
                            event_at='2024-04-10T00:00:00Z')
    new = store.create_claim('项目计划七月上线。', 'plan', '晚录入的延期通知', scope='release',
                             asserted_by='项目组', statement_at='2024-04-10T00:00:00Z')
    store.add_evidence(new['id'], notice.id, 'supports', '通知原文', quote='七月上线')
    store.revise_claim(old['id'], new['id'], '新通知修订了原计划')
    past = store.recall_claims(scope='release', as_of=checkpoint)['claims']
    current = store.recall_claims(scope='release')['claims']
    assert [c['id'] for c in past] == [old['id']]
    assert [c['id'] for c in current] == [new['id']]
    assert store.current_narrative(scope='release/product')['review_status'] == 'stale'
    assert store.current_narrative(scope='release/operations')['review_status'] == 'current'

    # Deliberately put the old material in the anchor set. Archive investigation
    # still has its own result budget; anchor priority cannot consume its slot.
    store.promote(original.id, '保存原计划')
    store.pin(original.id, '用于说明项目背景；置顶不表示当前计划仍是六月')
    archive = store.search_archive('七月 延期 通知', limit=1)
    assert archive['memories'][0]['id'] == notice.id
    return {
        '此前录入时点采用的计划': past[0]['content'],
        '现在采用的计划': current[0]['content'],
        '主张类型': current[0]['kind'],
        '原计划历史状态': store.inspect_claim(old['id'])['status'],
        '产品叙事': store.current_narrative(scope='release/product')['review_status'],
        '运维叙事': store.current_narrative(scope='release/operations')['review_status'],
        '独立档案检索': archive['memories'][0]['content'],
        '采集范围是否完整': archive['retrieval']['collection_completeness'],
        '边界': '示例验证存储协议；不能据此断言成员当时知情、全体同意或计划已完成。',
    }


if __name__ == '__main__':
    with TemporaryDirectory(prefix='historical-claims-') as directory:
        store = Store(Path(directory) / 'example.db')
        try:
            print(json.dumps(demonstrate(store), ensure_ascii=False, indent=2))
        finally:
            store.close()
