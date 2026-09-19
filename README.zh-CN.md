# Memory as History

> **[English](README.md)** | 简体中文

> 多数 Agent 记忆系统用时效性和相似度打分来决定"记住什么"。本项目借用记忆研究（memory studies）看待人类记忆的方式对待 Agent 记忆：记忆通过**刻意巩固**、**身份锚点**、**来源分级**与**负责任的遗忘**成为"历史"——而不只是存储和检索的副产品。

**代码版本：1.2.0rc1；事务、来源分级与解除锚定审计变更记录在 [Unreleased](CHANGELOG.md#unreleased)。CI：[![CI](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml/badge.svg)](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml)**

## 为什么做这个

存储和检索之外，还有一组需要明确回答的问题：哪些陈述值得长期保留、依据是谁提供的、何时停止适用、谁记录了这个决定？本项目让这些决定显式、可追溯；不声称其他记忆系统都缺少这些能力。

而记忆研究学界（Halbwachs、Nora、Assmann、Ricoeur）用一个世纪描述了人类记忆如何真正变得持久，答案从来不是"得分最高的事实自动存活"：

- 记忆是被**社会框架**建构的，不是私人录音（Halbwachs，*cadres sociaux*）
- 身份锚定在一小撮**记忆之场**上——它们不与日常回忆竞争注意力（Nora，*lieux de mémoire*）
- 持久的"文化记忆"要通过明确的**巩固**过程从日常"交往记忆"中沉淀——是一场仪式，不是一个阈值（Assmann）
- 记忆分层为**档案 / 证词 / 解释**，遗忘是必要且正当的，不是失败（Ricoeur，*La mémoire, l'histoire, l'oubli*）

本项目关注记忆成为持久历史的过程，以及证据变化后如何重新审视这段历史。

## 八个模块

| 模块 | 机制 | 理论来源 |
|---|---|---|
| **巩固协议** | 记忆初始为 `working`，只有显式 `promote(reason)` 才升格为 `consolidated`，理由必填并记入审计日志。永不自动升格。 | Assmann：交往记忆 → 文化记忆 |
| **锚点记忆** | 少数 `pin(reason)` 的记忆在 recall 时永远优先返回、不参与相关性竞争。**必须先巩固才能锚定**；超过软限制（12）返回警告。 | Nora：记忆之场 |
| **三级溯源** | 每条记忆带层级：`archive`（原始记录）/ `testimony`（独立来源佐证后自动升级）/ `interpretation`（AI 自身推断，必须定期 `review()` 复核，逾期标记 stale）。 | Ricoeur：档案 / 证词 / 解释 |
| **主动遗忘** | `forget(reason)` 打墓碑标记：内容保留不硬删，但从召回中消失；**锚点必须先解除才能遗忘**；`restore(reason)` 永远可逆。 | Ricoeur：遗忘的正当性 |
| **投毒防御** | 身份/权限/指令类记忆可标记 `security_sensitive`；此类记忆 `pin()` 时**强制要求来自不同来源的独立佐证**，否则拒绝并记 `pin_denied`。单次注入的伪造指令无法自我晋升为永久锚点。 | Ricoeur：记忆之滥用（*l'abus de mémoire*）/ 史料批判 |
| **叙事整合** | `narrate(content, reason, memory_ids?)` 给"综合叙事"一个版本化、可问责的存在：旧叙事标记 superseded 不删除——**叙事本身有历史**。`recall()` 返回离散事实与可用叙事；依赖失效后隐藏叙事正文，提示显式复核，历史文本仍保留。 | Ricoeur：叙事身份（*identité narrative*） |
| **经典/档案流转** | 任务级流动的"经典圈"（区别于永久锚点）：`canonize(scope, reason)` 纳入当前任务优先注入；任务结束 `end_scope(reason)` 整圈退场——不是遗忘也不是降级。解决上下文膨胀。 | Assmann：Kanon / Archiv |
| **社会框架** | 记忆可携带社会/关系框架标签；两条记忆冲突时 `mark_conflict(a, b, reason)` 显式声明、**两个版本都保留**；`resolve_conflict` 记录裁决但失败版本不删除。 | Halbwachs：社会框架 |

所有状态变更（promote / pin / unpin / corroborate / review / forget / restore / canonize / narrate / 标记敏感 / 声明冲突……）统一写入审计日志，带理由和时间戳——**什么成为了历史、为什么，全部可查询**。

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

- **349 个自动化测试**（含真实 MCP stdio 协议回归、事务故障与独立进程竞争测试）+ **6 项可靠性检查**（线程安全、多进程并发、重启持久化、5000 条规模、含 SQL 注入形态的边界输入、嵌套目录）。CI 保留 ubuntu/macos × Python 3.10–3.12 六矩阵，并增加 MCP 1.2.0、最新 1.x 和最新 2.x 的兼容性检查
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
