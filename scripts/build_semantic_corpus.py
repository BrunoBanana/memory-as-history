"""Freeze author-visible bilingual semantic development examples before scoring."""
import hashlib
import json
from pathlib import Path

# Each topic has two relevant facts, two distractors, a multi-evidence question
# and a single-evidence question. Translations are correlated, not a holdout.
TOPICS = [
    (
        ["Mira requests an aisle seat because she needs room to stretch her legs.",
         "For her flight meal, Mira asks for food without dairy products.",
         "Noah prefers a window seat on his flights.",
         "Mira bought a new blue suitcase for the journey."],
        ["米拉坐飞机时要求靠过道的位置，方便伸展双腿。",
         "米拉的机上餐食需要不含乳制品。",
         "诺亚坐飞机喜欢靠窗的位置。",
         "米拉为这次旅行买了一个蓝色行李箱。"],
        ["What seating and dietary accommodations does Mira need when flying?",
         "Which passenger must avoid milk in the airplane meal?"],
        ["米拉乘机时在座位和饮食方面有哪些要求？", "谁的航空餐需要避开牛奶？"], 1),
    (
        ["Before Atlas can go live, two engineers must sign the release checklist.",
         "An Atlas deployment must be reversed if the error rate exceeds two percent.",
         "The Atlas logo was redesigned in orange last week.",
         "The Birch service requires just one reviewer for documentation updates."],
        ["阿特拉斯上线之前，必须有两名工程师签署发布清单。",
         "阿特拉斯发布后如果错误率超过百分之二，必须回滚。",
         "阿特拉斯的标志上周改成了橙色。",
         "白桦服务的文档更新只需要一名审核人。"],
        ["What approval and rollback conditions govern an Atlas launch?",
         "When must an Atlas rollout be undone?"],
        ["阿特拉斯发布需要满足哪些审批和回退条件？", "阿特拉斯上线后什么情况下需要撤回版本？"], 1),
    (
        ["Leena practices the cello every Tuesday evening.",
         "On Saturday mornings Leena sings with the neighborhood choir.",
         "Leena's brother plays football on Tuesdays.",
         "Owen has piano lessons on Sunday afternoon."],
        ["莉娜每周二晚上练习大提琴。", "每周六上午，莉娜都参加社区合唱团。",
         "莉娜的哥哥每周二踢足球。", "欧文在周日下午上钢琴课。"],
        ["Which two musical activities are part of Leena's weekly routine?",
         "When does Leena rehearse with other singers?"],
        ["莉娜每周固定参加哪两项音乐活动？", "莉娜什么时候和其他人一起练唱？"], 1),
    (
        ["The Cedar reading club selected a biography of a polar explorer for October.",
         "For November the Cedar reading club chose a collection of short stories.",
         "The Maple reading club chose a cookbook for October.",
         "The Cedar reading club meets in the library basement."],
        ["雪松读书会十月选的是一本极地探险家的传记。", "雪松读书会十一月选了一本短篇小说集。",
         "枫树读书会十月选了一本食谱。", "雪松读书会在图书馆地下室聚会。"],
        ["What genres did the Cedar club pick for October and November?",
         "Which reading group chose a life story about exploring the polar regions?"],
        ["雪松读书会十月和十一月分别阅读哪类书？", "哪个读书会选择了讲述极地探险人生的书？"], 0),
    (
        ["In 2023, Jun lived in a rented apartment in Porto.",
         "Jun relocated to a house in Helsinki in February 2025.",
         "Jun visited Kyoto on holiday in 2024 but did not move there.",
         "Jun's friend Sam has lived in Porto since 2020."],
        ["2023年，俊住在波尔图的一套出租公寓里。", "2025年2月，俊搬到了赫尔辛基的一栋房子里。",
         "俊在2024年度假时去过京都，但没有搬到那里。", "俊的朋友山姆从2020年起一直住在波尔图。"],
        ["Where did Jun live before and after his February 2025 move?",
         "In which city was Jun renting a home in 2023?"],
        ["俊在2025年2月搬家前后分别住在哪里？", "俊在2023年在哪个城市租房住？"], 0),
    (
        ["Order ZX-417 contains twelve replacement keyboard switches.",
         "Order ZX-417 is scheduled to arrive on June 14.",
         "Order ZX-471 contains a computer monitor and arrives on June 19.",
         "The warehouse stocks twelve different types of mouse pad."],
        ["订单ZX-417包含十二个替换键盘轴体。", "订单ZX-417预计6月14日送达。",
         "订单ZX-471包含一台显示器，预计6月19日送达。", "仓库有十二种不同的鼠标垫。"],
        ["What was ordered under ZX-417 and when should it arrive?",
         "What is the expected delivery date for ZX-417?"],
        ["ZX-417买了什么，预计什么时候到？", "ZX-417预计哪天送达？"], 1),
    (
        ["The orchard workshop asks volunteers to bring waterproof boots.",
         "For the orchard workshop, volunteers should also pack reusable drinking bottles.",
         "The museum workshop asks visitors to bring sketchbooks.",
         "The orchard workshop registration closes on Friday."],
        ["果园工作坊要求志愿者带防水靴。", "参加果园工作坊的志愿者还需要自备可重复使用的水瓶。",
         "博物馆工作坊要求参观者带素描本。", "果园工作坊的报名周五截止。"],
        ["What footwear and drink container should orchard volunteers bring?",
         "What should volunteers wear to keep their feet dry at the orchard?"],
        ["果园志愿者应该准备什么鞋子和饮水容器？", "果园志愿者要穿什么才能避免脚被打湿？"], 0),
    (
        ["Nora's essay argues that museum displays must explain where objects came from.",
         "Nora's essay also asks museums to acknowledge competing interpretations of the past.",
         "Nora designed a poster about the museum's opening hours.",
         "Eli's essay is about lowering museum ticket prices."],
        ["诺拉的文章主张博物馆展陈必须交代文物来源。", "诺拉的文章还要求博物馆承认对过去存在不同解释。",
         "诺拉设计过一张关于博物馆开放时间的海报。", "伊莱的文章讨论降低博物馆门票价格。"],
        ["Which two principles does Nora propose for presenting history in museums?",
         "Who wants exhibitions to disclose the origin of their artifacts?"],
        ["诺拉提出了哪两条在博物馆呈现历史的原则？", "谁主张展览应说明藏品的出处？"], 0),
]


def build():
    data = []
    for language in ('en', 'zh'):
        turns, questions = [], []
        for index, (en, zh, q_en, q_zh, single) in enumerate(TOPICS):
            ids = [f'D1:{index * 4 + offset + 1}' for offset in range(4)]
            turns.extend({'dia_id': mid, 'speaker': 'Notes' if language == 'en' else '记录', 'text': text}
                         for mid, text in zip(ids, en if language == 'en' else zh))
            q = q_en if language == 'en' else q_zh
            questions.extend([{'question': q[0], 'evidence': ids[:2], 'category': 1},
                              {'question': q[1], 'evidence': [ids[single]], 'category': 4}])
        data.append({'sample_id': f'semantic-dev-{language}',
                     'conversation': {'session_1': turns}, 'qa': questions})
    return data


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'src/memory_as_history/benchmarks/data'
    raw = (json.dumps(build(), ensure_ascii=False, indent=2) + '\n').encode()
    (target / 'semantic-dev-v1.json').write_bytes(raw)
    manifest = {'name': 'semantic-dev-v1', 'license': 'MIT', 'sha256': hashlib.sha256(raw).hexdigest(),
                'documents': 64, 'questions': 32, 'languages': ['en', 'zh'],
                'provenance': 'Eight authored topic groups with paired translations. Author-visible development only; not a blind holdout. Frozen before model scoring.',
                'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (target / 'semantic-dev-v1.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
