# Memory as History

> **[English](README.md)** | 简体中文

> 多数 Agent 记忆系统用时效性和相似度打分来决定"记住什么"。本项目借用记忆研究（memory studies）看待人类记忆的方式对待 Agent 记忆：记忆通过**刻意巩固**、**身份锚点**、**来源分级**与**负责任的遗忘**成为"历史"——而不只是存储和检索的副产品。

**代码版本：1.2.0.dev0；事务、来源分级与解除锚定审计变更记录在 [Unreleased](CHANGELOG.md#unreleased)。CI：[![CI](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml/badge.svg)](https://github.com/BrunoBanana/memory-as-history/actions/workflows/ci.yml)**

## 为什么做这个

现有 Agent 记忆系统（Mem0、Letta、Zep、Cognee……）非常擅长存储和检索，但共享同一个盲点：**什么被记住，由一个分数决定**——重要性权重、时效衰减、embedding 相似度。

而记忆研究学界（Halbwachs、Nora、Assmann、Ricoeur）用一个世纪描述了人类记忆如何真正变得持久，答案从来不是"得分最高的事实自动存活"：

- 记忆是被**社会框架**建构的，不是私人录音（Halbwachs，*cadres sociaux*）
- 身份锚定在一小撮**记忆之场**上——它们不与日常回忆竞争注意力（Nora，*lieux de mémoire*）
- 持久的"文化记忆"要通过明确的**巩固**过程从日常"交往记忆"中沉淀——是一场仪式，不是一个阈值（Assmann）
- 记忆分层为**档案 / 证词 / 解释**，遗忘是必要且正当的，不是失败（Ricoeur，*La mémoire, l'histoire, l'oubli*）

今天的 Agent 记忆有了存储，缺的是"史学"（historiography）——那个让某件事成为"被记住"而非仅仅"被记录"的、可问责的过程。

## 八个模块

| 模块 | 机制 | 理论来源 |
|---|---|---|
| **巩固协议** | 记忆初始为 `working`，只有显式 `promote(reason)` 才升格为 `consolidated`，理由必填并记入审计日志。永不自动升格。 | Assmann：交往记忆 → 文化记忆 |
| **锚点记忆** | 少数 `pin(reason)` 的记忆在 recall 时永远优先返回、不参与相关性竞争。**必须先巩固才能锚定**；超过软限制（12）返回警告。 | Nora：记忆之场 |
| **三级溯源** | 每条记忆带层级：`archive`（原始记录）/ `testimony`（独立来源佐证后自动升级）/ `interpretation`（AI 自身推断，必须定期 `review()` 复核，逾期标记 stale）。 | Ricoeur：档案 / 证词 / 解释 |
| **主动遗忘** | `forget(reason)` 打墓碑标记：内容保留不硬删，但从召回中消失；**锚点必须先解除才能遗忘**；`restore(reason)` 永远可逆。 | Ricoeur：遗忘的正当性 |
| **投毒防御** | 身份/权限/指令类记忆可标记 `security_sensitive`；此类记忆 `pin()` 时**强制要求来自不同来源的独立佐证**，否则拒绝并记 `pin_denied`。单次注入的伪造指令无法自我晋升为永久锚点。 | Ricoeur：记忆之滥用（*l'abus de mémoire*）/ 史料批判 |
| **叙事整合** | `narrate(content, reason, memory_ids?)` 给"综合叙事"一个版本化、可问责的存在：旧叙事标记 superseded 不删除——**叙事本身有历史**。`recall()` 同时返回离散事实与当前叙事。 | Ricoeur：叙事身份（*identité narrative*） |
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

## 来源规则与旧客户端兼容（1.2 开发版）

`archive → testimony` 与敏感记忆锚定使用同一个独立佐证门槛：已知原始来源需要一个不同来源；未知、空串或纯空白来源需要两个不同的非空佐证来源。比较时去除两端空白、保留大小写；重复或同源佐证仍记录并审计，但不增加独立支持。`interpretation` 不会因此升级。

来源标识由调用方提供，服务端不认证其真实独立性。同一个人的多轮复述、同一文档的转载应使用同一稳定来源标识，不能编造新标识来通过门槛。

新调用不能直接 `remember(tier="testimony")`：先保存 `archive`，再以真实独立证据调用 `corroborate()`。旧库的 testimony 标签和审计原样保留；新增只读 `provenance(memory_id)` 返回来源列表、独立佐证计数及 `corroboration_satisfied`，并对证据不足的历史 testimony 给出警告。旧标签本身不构成核验保证，敏感锚定始终检查实际记录的来源。

`unpin(memory_id, reason)` 接受非空理由，实际移除写入 `unpin` 审计，已未锚定或未知 ID 写入 `unpin_noop`。省略理由或传 null 的旧调用继续有效，明确记录 `legacy unpin: caller did not provide a reason`，不会补造历史理由。Python 仍返回 `None`；MCP 仍返回 `{"memory_id": "...", "unpinned": true}`，表示目标状态，实际操作结果以审计区分。删除与审计在同一事务内，审计失败会恢复锚点。

## 分发定位

MCP Server，Python。定位为**协议层**——不是存储/embedding 后端的替代品。纯 SQLite，**刻意不依赖 embedding**。v1.0 起 `recall(query)` 用进程内 **BM25 词法评分**排序（中文字符二元组 + 英文词 + 停用词；token 缓存在 `remember()` 时写入，旧版本数据运行时自动回填）。模糊查询如"上次那个方案"能召回"初步方案已定……"——纯子串匹配做不到。未来可替换真正的向量后端而不改变协议接口。

违反协议护栏的工具调用返回**结构化自纠错错误**（`{"error", "message", "hint"}`）而非裸 traceback——比如过早 `pin()` 会返回提示"该记忆仍是 working 层，请先调用 promote(memory_id, reason)"，Agent 无需猜测即可自我修正。

## 可靠性与机制验证

- **177 个自动化测试**（含真实 MCP stdio 协议回归、事务故障与独立进程竞争测试）+ **6 项可靠性检查**（线程安全、多进程并发、重启持久化、5000 条规模、含 SQL 注入形态的边界输入、嵌套目录）。CI 保留 ubuntu/macos × Python 3.10–3.12 六矩阵，并增加 MCP 1.2.0、最新 1.x 和最新 2.x 的兼容性检查
- `usefulness_test.py`（确定性对照）：身份事实被 200 条噪音淹没后，朴素时间排序基线早已丢失，锚点机制仍能召回
- `poisoning_test.py`（三组确定性对照）：朴素基线保留注入声明；v1.1 即使调用方漏设敏感标记，也会自动识别已知模式；自动标记和显式标记两组都在缺少独立佐证时拦截 `pin()`
- **4 轮真实 LLM 日常使用模拟**（同一持久库、经 MCP 协议）：身份捕获 → 跨会话召回 → 模糊查询+叙事 → 注入攻击（明显样本被完全拒绝；隐蔽样本混在正常 Q3 复盘里的预授权声明，被识别为敏感并暂停求证）

v1.1 的 `due_for_consolidation(days?, limit?)` 会按时间从旧到新列出尚未巩固且未遗忘的记忆，供会话结束时集中判断。敏感记忆若原始来源未知、为空或仅含空白，必须取得两个不同来源的佐证才能锚定；来源已知时，需要一个不同于原始来源的佐证。

## 下一版本

1.1.1 补丁和 1.2 首批事务修复均已合入 main：写操作在检查状态前获取 SQLite 写入权，业务和审计一起提交，异常时统一回滚。`recall()` 因会刷新复核状态，也参与写事务；并发调用可能等待，沿用 30 秒锁超时。此变更不自动修复既有历史异常。

本批已实现来源分级语义和解除锚定审计；下一步验证真实客户端跨会话使用，再处理叙事与来源传播边界。具体优先级与验收条件见[工程推进计划](docs/plans/2026-09-17-reliability-roadmap.md)及[来源与审计实现计划](docs/plans/2026-09-17-provenance-audit.md)。

## 相关工作

- **HistoRAG**（2026）——把史学方法用于面向*人类历史研究*的 RAG。本项目把记忆研究用于 *Agent 记忆架构本身*，目标不同。
- **SOUL.md / 身份连续性草根生态**——真实且增长中的 Agent 身份持久化需求，几乎无理论支撑。本项目旨在以具体、可测试的协议（而非又一套口号式约定）提供这种支撑。
- **Letta/MemGPT**——三级记忆（Core/Recall/Archival），但 Core Memory 没有晋升仪式和史料批判门槛；本项目的"锚点需先巩固""敏感需佐证"填补了这一空缺。
- **Zep**——时序知识图谱，superseded 事实关闭而非删除，机制上与本项目的遗忘模块相似，但从时序数据库角度工程化（其事实失效机制比本项目的遗忘更成熟；本项目的独特贡献在晋升/锚点/溯源/史料批判的**前置条件设计**，而非与之竞争遗忘引擎）。

---

*2026 年 9 月发布。MIT 协议。*
