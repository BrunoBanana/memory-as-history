# Memory as History

> **[English](README.md)** | 简体中文

Memory as History 保存**说过什么、主张依据什么，以及被采用的认识如何改变**。
项目提供基于 SQLite 的 MCP 服务，让巩固、来源判断、修订、叙事版本与遗忘都有明确记录。

**代码版本：1.3.0a1（未发布预览版）；变更见 [Changelog](CHANGELOG.md#unreleased)。
CI：[![CI](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml/badge.svg)](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml)**

## 为什么做这个

一个陪你工作了几个月的助手，告诉你一件关于你自己项目的事。你问它这是哪来的。它说不出来——不是在回避，而是它留下的只是一句分数够高、因而活到今天的话。没有任何记录说明是谁说的、这句话支持哪个判断、有没有相反的材料、以及为什么它还在被重复。

所有把"留下什么"当成排序问题的记忆设计，最后都会走到这里。重要性权重、时效衰减、向量相似度回答的都是*"我该保留什么"*，没有一个回答*"我凭什么这么认为，什么会让我改变判断"*。第二个问题不是存储问题——它是历史学家围绕它建立起整个学科的问题，而他们手里的材料残缺不全、写作者有立场、彼此互相抄录，并且无法追问。

所以这个项目借来的是他们的方法，而不是一个更好的打分函数：把文献的出处与它的真实性分开（布洛赫）、注意同一份公告的三份抄件是一个证人而非三个、判断改变时保留被取代的旧叙述、把沉默看作被生产出来的而非偶然的（特鲁约）。**记忆**是当下与过去之间有选择的关系；**历史**是一份可问责的、关于过去的叙述。Agent 拥有前者，需要的是后者。→ **[为什么是历史，而不只是记忆](docs/why-history.zh-CN.md)**

保存下来的文字可能是旧计划、自述、引文或解释。重要不等于真实，较新的说法也不能解释为什么改变了旧判断。
本项目保留原材料，同时让证据、采纳决定和修订过程可以检查。

历史学与记忆研究提供了值得借鉴的问题：如何批判来源、理解社会视角、区分活跃使用与档案保存，以及允许重释。
这里的实现是工程选择，不是人类记忆的原样模型，也不是 Halbwachs、Nora、Assmann 与 Ricoeur 共同提出的统一算法。
尤其是旧接口的 `archive/testimony/interpretation` 标签，**不等于 Ricoeur 的历史认识三阶段**。
理论出处、区别及选读范围见[阅读报告](docs/research/2026-09-19-history-memory-reading.md)。

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
- 首次使用需在客户端给该 server 的工具授权（如 Claude Code 会弹提示）——所有第三方 MCP server 的通用流程
- 仓库里的 `AGENT_GUIDE.md` 是可直接粘贴到系统提示词的**工具使用指引**（含中文触发词映射，如"重要/别丢/一直记住" → promote+pin）。实测表明：只靠工具描述，Agent 不会可靠地自主调用 `promote`/`pin`，加上指引后明显改善

单独运行 server（stdio）：

```bash
python -m memory_as_history.server
```

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
