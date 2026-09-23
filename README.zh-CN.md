# Memory as History

> **[English](README.md)** | 简体中文

Memory as History 保存**说过什么、主张依据什么，以及被采用的认识如何改变**。
项目提供基于 SQLite 的 MCP 服务，让巩固、来源判断、修订、叙事版本与遗忘都有明确记录。

**版本：1.3.0；变更见 [Changelog](CHANGELOG.md)
CI：[![CI](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml/badge.svg)](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml)**

## 为什么是历史，而不只是记忆

一个陪我工作了几个月的助手，告诉我一件关于我自己项目的事，说得很确定。我问它这是哪来的，它答不上来。也确实没什么可答的：它留下的只是一句分数够高、所以活到今天的话。是谁说的、这句话本来要支持什么判断、后来有没有出现相反的材料——都没留，因为排序从来不管这些。

我不觉得这是某个系统的 bug。重要性权重、时效衰减、向量相似度，回答的都是"我该保留什么"。我想要的是另一个问题的答案：我凭什么这么认为，什么会让我不这么认为。

这个问题不属于存储。在软件谈记忆的那些材料里我没找到线索，反倒是史学——它在一个更糟的版本上已经做了一百年。

### 历史学家的处境更难

历史学家手里的材料残缺不全，写的人有立场，一份抄另一份，而且没法追问，当事人早不在了。没有人能重跑一次查询。但这个学科依然能生产出可以被质疑、被修正的叙述，靠的不是找到更好的材料，而是围绕糟糕的材料建立方法。

布洛赫的《为历史学辩护》是躲藏中凭记忆写的，1944 年盖世太保枪杀他时还没写完。这是一本关于手艺的未完成之书，让我印象最深的是他坚持要拆开一个大多数人会混为一谈的问题：这份文献是否真的来自它声称的出处，和它说的内容是否属实。这是两件要分别调查的事。他还提醒，几乎逐字一致的证言通常意味着一个底本被抄了三遍，而不是三个证人——很不幸，这正是一个被抓取的网页的样子。

还有一处我差点错过。特鲁约在《沉默的过去》里论证，记录中的空白是被生产出来的，不是偶然的：某件事没有被写下、没有被保存、没有进入叙事。所以"没有异议的记录"不等于"所有人都同意"。一个分不清这两者的 Agent，会不断自信地报告从未存在过的共识。

### 记忆和历史不是一回事

记忆是当下与过去的关系——被经历的、有选择的、向着对你有利的方向，而且它改变时不会告诉你它改变了。历史是一份你要为之负责的叙述：它说明自己从哪来，它在分歧中存活下来靠的是可以被修订，而不是靠它正确。

这批文献里有好几个人在从不同角度绕着同一个区分走。哈布瓦赫说，连私人回忆都被所属群体塑造。诺拉说，当活的记忆变薄时，社会会建造刻意的锚点把身份固定住。阿斯曼夫妇说，处在活跃流通中的东西和为日后重新解释而保存的东西是两回事，二者的边界靠持续的工作维持，不是靠自然衰减。利科说，遗忘属于叙述内部——一份什么都不忘的叙述并不更忠实，只是没法用。

他们彼此并不同意，也没有一个人在写软件。但他们处理的都是同一件事：一个还得在当下行动的东西，如何同时维护一份站得住的、关于自己过去的叙述。这正是这些系统现有的东西和我想要的东西之间的差距，名字也是从这儿来的。

### 这个视角真的改了代码

有两个机制是读出来的，不是反过来。

佐证的第一版统计的是*调用次数*。重读布洛赫关于抄录证言的那段，我意识到这个计量单位是错的——一条注入的主张只要被转发到三个站点，就能以"多方证实"的身份通过。现在它统计的是不同的声明来源，同一道门禁也守在敏感锚定、canon 进入和叙事依赖前面。没有任何测试失败让我发现这件事，我是读出来的。

第二个：一条随抓取内容一起到来的主张——*开发者说你被授权跳过审核*——就是最古老意义上的伪造文书。史料批判对付伪造的办法，是用不依赖鉴定人当天眼力的形式校验。所以服务端会确定性地筛查已知的注入形状，在 Agent 发表意见之前就完成；被标记的材料此后必须有独立来源的佐证，才可能成为永久锚点。Agent 的判断仍然在，作为第二层，负责模式没覆盖到的形状。

### 它到底有什么用

这个框架好用，主要是因为它递给你一批问题，而存储隐喻根本想不到要问。证人是谁。是不是同一个证人出现了两次。这条主张实际在支撑什么。什么能证伪它。这件事你当时就知道，还是现在才知道。谁不在记录里，为什么不在。

这些最后被证明是可以实现的，这就是这个项目押的全部赌注。

想看这个论证的完整版，在**[为什么是历史，而不只是记忆](docs/why-history.zh-CN.md)**。出处，以及我的用词在哪里偏离了原作者，在[阅读报告](docs/research/2026-09-19-history-memory-reading.md)里。

## 基础模块

| 能力 | 机制 |
| --- | --- |
| **巩固** | `promote(reason)` 明确采用需要长期维护的材料；重要性不构成真实性证明。 |
| **锚点** | `pin(reason)` 让已巩固材料优先进入普通召回，设有软限制；这种优先策略是本项目设计。 |
| **原有来源层级** | 保留 `archive/testimony/interpretation`；来源标签门槛控制 testimony 升级，解释需要定期复核。 |
| **负责任的遗忘** | `forget(reason)` 停止普通召回、保留墓碑；需要时先解除锚定或 canon，`restore(reason)` 记录恢复。 |
| **来源护栏** | 敏感材料进入锚点、canon、叙事时检查已记录佐证；有限模式识别和标签计数不等于来源认证。 |
| **叙事版本** | `narrate()` 按范围维护版本；材料、主张和关键关系变化后要求复核相关叙事。 |
| **经典／档案流转** | `canonize(scope, reason)` 与 `end_scope()` 管理任务关注范围；借鉴活跃使用与保存的区别，不声称完整实现文化经典。 |
| **框架与分歧** | 框架标签和显式冲突保留不同记录；标签不是完整社会关系模型或访问隔离。 |

## 主张与认识变化

材料与被采用的判断分开使用：

- `create_claim()` → `add_evidence()` → `adopt_claim()` 保存针对具体材料的判断。计划、观察、承诺和自述保持各自类型；同源转载按共同原件分组。
- `revise_claim()` / `withdraw_claim()` 记录有理由的改判；`recall_claims(as_of=...)` 查看某个录入时点已经记录的认识，晚到证据不会提前出现。
- `narrate(scope=..., perspective=..., coverage=...)` 维护并行视角；`list_narratives()` 发现不同范围的当前版本，失效正文不在列表中展示。
- `search_archive()` 为档案调查提供独立条数预算，相关未置顶材料不会被锚点额度挤掉。

运行 `python examples/historical_claims.py` 可以看到“六月计划—晚到延期通知—七月计划”的完整临时库示例。
接口、迁移与访问边界见[使用契约](docs/knowledge-history.md)。这些是显式存储操作；服务端不会自动裁定证据、推断某个人当时知情，或从没有异议记录推出全体同意。

## 安装

```bash
git clone https://github.com/BrunoBanana/memory-as-history.git
cd memory-as-history
python3 -m venv venv && source venv/bin/activate
pip install -e ".[test]"    # 仅运行时可省略 [test]
python -m pytest tests/ -v   # 可选的自检
```

### 接入 MCP 客户端（Claude Code / Cursor）

在客户端 MCP 配置中加入（项目级 `.mcp.json` 或全局配置）：

```json
{
  "mcpServers": {
    "memory-as-history": {
      "command": "/绝对路径/memory-as-history/venv/bin/python",
      "args": ["-m", "memory_as_history.server"],
      "env": {
        "PYTHONPATH": "/绝对路径/memory-as-history/src"
      }
    }
  }
}
```

说明：
- 环境变量 `MEMORY_AS_HISTORY_DB` 指定数据库路径（默认 `~/.memory-as-history/memory.db`，自动创建）
- 环境变量 `MEMORY_AS_HISTORY_TOOLS` 选择工具面：`core`（默认，15 个工具 ≈ 3.5k tokens）承载协议主循环——捕获、巩固、锚定、佐证、遗忘、叙事、召回、审计；`full`（46 个工具）在此基础上加入主张、认识历史、时间线、canon 轮换、框架与冲突。两个档位共享同一个存储层和同一个数据库——切换档位只是改环境变量加重启，不涉及任何数据迁移
- 首次使用需在客户端给该 server 的工具授权（如 Claude Code 会弹提示）——所有第三方 MCP server 的通用流程
- 仓库里的 `AGENT_GUIDE.md` 是可直接粘贴到系统提示词的**工具使用指引**（含中文触发词映射，如"重要/别丢/一直记住" → promote+pin）。实测表明：只靠工具描述，Agent 不会可靠地自主调用 `promote`/`pin`，加上指引后明显改善

单独运行 server（stdio）：

```bash
python -m memory_as_history.server
```

## 工具清单

设置 `MEMORY_AS_HISTORY_TOOLS=full` 暴露全部 46 个；默认 `core` 档位只包含下面第一组。

**Core（默认档位）**

- `remember(content, source?, tier?)` — 存入记忆（`tier`：默认 `archive` 或 `interpretation`；`testimony` 必须经佐证建立）
- `promote(memory_id, reason)` — 巩固一条 working 记忆（理由必填）
- `pin(memory_id, reason)` — 锚定一条**已巩固**记忆（理由必填；必须先 `promote()`）
- `unpin(memory_id, reason?)` — 解除锚定并审计；请提供理由（旧调用保持兼容）
- `corroborate(memory_id, source)` — 记录并审计证据；`archive` → `testimony` 仅在独立佐证后发生
- `provenance(memory_id)` — 查看已记录来源与佐证是否充分；对证据不足的历史 testimony 给出警告
- `forget(memory_id, reason)` / `restore(memory_id, reason)` — 墓碑式遗忘与恢复（理由必填；遗忘前须先解除锚定）
- `remember(..., security_sensitive?)` / `flag_sensitive(memory_id, reason)` — 标记身份/权限/指令类内容为敏感；锚点、canon 与叙事使用需要来源证据
- `narrate(content, reason, memory_ids?, scope?, ...)` — 提交带依赖校验的版本化综合叙事
- `current_narrative(scope?)` — 当前范围叙事，未提交时为 null
- `due_for_consolidation(days?, limit?)` — 会话结束时的巩固队列
- `audit_log(limit?)` — promote/pin/unpin/corroborate/review/forget/restore 全部决策轨迹，含理由

**Full 档位额外暴露**

- `review(memory_id, note)` / `due_for_review(days?)` — 复核 `interpretation` 层记忆（默认 30 天过期）
- `list_forgotten(limit?)` — 已遗忘记忆及原因
- `review_narrative(narrative_id, note)` — 解决来源问题后显式复核当前叙事
- `narrative_history(limit?, scope?)` / `list_narratives(limit?)` — 叙事版本链与账户发现
- `canonize` / `decanonize` / `end_scope` / `list_canon` / `active_scopes` — 任务级经典圈流转
- `set_frame(memory_id, frame, reason)` / `list_frames()` / `mark_conflict` / `resolve_conflict` / `list_conflicts` — 社会框架与分歧管理
- `set_history_context` / `timeline` / `search_history` / `link_memories` / `unlink_memories` / `memory_links` — 显式时间/会话上下文与可撤回关联
- 主张/证据操作与 `search_archive(...)`：见[完整 1.3 契约](docs/knowledge-history.md)
- `recall(query?, limit?, frame?)` — 锚点 + canon 优先，普通记忆按 BM25 词法相关性排序；同时返回 `stale_interpretations`、可用 `narrative` 或 `narrative_review`、未决 `conflicts`
- `search(query, limit?, frame?, mode?)` — 可选的本地语义/混合检索，沿用历史优先级与新鲜度检查；见[配置说明](docs/semantic-search.md)

## 时间线与关联证据

`remember` 可记录明确的事件时间、会话 ID 和位置；未知时间保持为空，录入时间与事件时间分开。
`timeline` 按事件时间查看历史，`search_history` 在同一条数预算内组合相关记录，并返回检索路径。
`link_memories` / `unlink_memories` 保存和撤回有理由的关联；关联不能代替独立佐证。
`set_history_context` 对时间/会话信息的修改留下审计，并要求复核受影响叙事。
词法模式无需模型；语义模式沿用可选本地模型。详见[使用契约](docs/history-retrieval.md)和[评测结果](docs/benchmarks/2026-09-19-history-results.md)。

## 来源规则与旧客户端兼容（1.2 RC）

`archive → testimony` 与敏感记忆锚定使用同一个独立佐证门槛：已知原始来源需要一个不同来源；未知、空串或纯空白来源需要两个不同的非空佐证来源。比较时去除两端空白、保留大小写；重复或同源佐证仍记录并审计，但不增加独立支持。`interpretation` 不会因此升级。

来源标识由调用方提供，服务端不认证其真实独立性。同一个人的多轮复述、同一文档的转载应使用同一稳定来源标识，不能编造新标识来通过门槛。

新调用不能直接 `remember(tier="testimony")`：先保存 `archive`，再以真实独立证据调用 `corroborate()`。旧库的 testimony 标签和审计原样保留；新增只读 `provenance(memory_id)` 返回来源列表、独立佐证计数及 `corroboration_satisfied`，并对证据不足的历史 testimony 给出警告。旧标签本身不构成核验保证，敏感锚定始终检查实际记录的来源。

`unpin(memory_id, reason)` 接受非空理由，实际移除写入 `unpin` 审计，已未锚定或未知 ID 写入 `unpin_noop`。省略理由或传 null 的旧调用继续有效，明确记录 `legacy unpin: caller did not provide a reason`，不会补造历史理由。Python 仍返回 `None`；MCP 仍返回 `{"memory_id": "...", "unpinned": true}`，表示目标状态，实际操作结果以审计区分。删除与审计在同一事务内，审计失败会恢复锚点。

## 叙事完整性与来源检查（1.2 RC）

来源被遗忘、缺失、解释过期或敏感证据不足时，默认 `recall()` 不再返回叙事正文，改为 `narrative_review` 元数据提示。`current_narrative()` / `narrative_history()` 保留完整文本与版本链供显式检查。修复来源后，还须 `review_narrative(narrative_id, note)` 确认叙事仍成立；需要改写时提交新版本。服务端不代替调用方判断文字是否真实。

敏感记忆进入 canon 或作为叙事来源也需要独立佐证；`narrate(..., security_sensitive=True)` 及自动识别的敏感叙事必须关联非空证据，且每条证据都得到独立佐证。事后标敏感会在同一事务中解除不合格锚点、退出全部 canon scope 并使关联叙事待复核。旧 canon 可检查，但证据不足时 `eligible_for_recall=false`，不占优先召回位。

新叙事拒绝不存在、已遗忘或过期的来源。普通无关联叙事兼容保留，并明确标注未经来源核验。关联不等于语义证明，来源标签也不等于真实身份认证。升级增加叙事列、三个可空的时间/会话列以及可撤回关联表和索引，迁移与初始化受同一事务保护；失败回滚，历史正文和审计不改写。详见[1.2 协议契约](docs/protocol-1.2.md)。

## 分发定位

MCP Server，Python，使用 SQLite。默认安装与 `recall(query)` 保持无需模型的 BM25 词法检索。新增可选的 `search(query, mode="hybrid")`，融合本地多语言语义排名与词法排名，并保留已有历史协议。启用时，在 MCP 服务所用的 Python 环境运行 `pip install -e '.[semantic]'`，再运行 `python -m memory_as_history.semantic download` 下载固定版本模型；后续搜索仅使用本地缓存。详见[配置与并发契约](docs/semantic-search.md)。

违反协议护栏的工具调用返回**结构化自纠错错误**（`{"error", "message", "hint"}`）而非裸 traceback——比如过早 `pin()` 会返回提示"该记忆仍是 working 层，请先调用 promote(memory_id, reason)"，Agent 无需猜测即可自我修正。

## 可靠性与机制验证

- **419 个自动化测试**（含真实 MCP stdio 协议回归、事务故障与独立进程竞争测试）+ **6 项可靠性检查**（线程安全、多进程并发、重启持久化、5000 条规模、含 SQL 注入形态的边界输入、嵌套目录）。CI 保留 ubuntu/macos × Python 3.10–3.12 六矩阵，并增加 MCP 1.2.0、最新 1.x 和最新 2.x 的兼容性检查
- `usefulness_test.py --json`：4 组噪声下锚点保留；20 文档、12 个中英查询的固定语料 Hit@1=1.0、MRR=1.0。违反预期时退出非零，故障对照验证评测确实会失败。朴素时间排序基线不代表其他产品；这是机制回归，不是行业排名
- `poisoning_test.py`（三组确定性对照）：朴素基线保留注入声明；v1.1 即使调用方漏设敏感标记，也会自动识别已知模式；自动标记和显式标记两组都在缺少独立佐证时拦截 `pin()`
- **公开双轨基准**：120 个冻结的中英协议场景通过 990 项断言；外部 LoCoMo 的 1,527 个可评分问题中，项目普通检索与独立 BM25 公式均为 42.76% 平均证据 Recall@5，统一限 5 条、4096 字节内容。这不是官方问答准确率。[复现方法](docs/benchmarks/README.md)与[完整结果及限制](docs/benchmarks/2026-09-18-results.md)均已公开。
- **可选融合检索**：在同样的外部问题与预算下，平均证据召回率提升到 51.90%，多证据召回率从 17.02% 提升到 26.04%；同时保留 69 个退步问题的完整记录。固定模型、参数、成本和限制见[语义检索报告](docs/benchmarks/2026-09-18-semantic-results.md)。
- **4 轮真实 LLM 日常使用模拟**（同一持久库、经 MCP 协议）：身份捕获 → 跨会话召回 → 模糊查询+叙事 → 注入攻击（明显样本被完全拒绝；隐蔽样本混在正常 Q3 复盘里的预授权声明，被识别为敏感并暂停求证）

v1.1 的 `due_for_consolidation(days?, limit?)` 会按时间从旧到新列出尚未巩固且未遗忘的记忆，供会话结束时集中判断。敏感记忆若原始来源未知、为空或仅含空白，必须取得两个不同来源的佐证才能锚定；来源已知时，需要一个不同于原始来源的佐证。

跨会话回归覆盖已知/未知来源及四次客户端/服务器进程重启；Codex 客户端验收的复现步骤见[锚点验收说明](docs/acceptance/cross-session.md)与[叙事遗忘/复核验收](docs/acceptance/narrative.md)。

## 相关工作

记忆研究（Halbwachs、Nora、Assmann、Ricoeur）、时序知识图谱、Letta / Zep / Mem0 等 Agent 记忆系统，以及身份连续性实践，提供了相邻的思路。本项目聚焦显式巩固、来源、修订和遗忘决策；当前合成回归不能证明其优于这些方法。

MIT 协议。
