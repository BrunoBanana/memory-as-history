# AML 接入层设计（Agent Memory Challenge 2026 II · 文本赛道）

> 目标：为 memory-as-history 增加符合 Agent Memory Leaderboard（AML）Add/Search
> 接入规范的 HTTP 服务，以「开源方法榜 · 文本记忆赛道」参赛。
> 状态：设计稿；实现以本文档为准，偏差需在此登记。

## 1. 背景与约束

- 比赛由 CSIG 主办（南京大学、浙江大学、Datawhale 承办），第二期于
  2026-09-20 开放，材料截止 2026-10-31 23:59，评测停止 2026-11-04 23:59，
  榜单 11 月中旬发布。
- 第二期不接受仅提交仓库或 Docker 镜像并由平台代部署；参赛方必须自行部署
  公网可达的 HTTPS Add/Search HTTP 接口。
- 本项目报名文本赛道（长对话、跨会话历史、事实、多跳关系、时间事件、
  个性化、规则与记忆治理，含 Streaming 持续记忆）。
- 文本赛道正式 Top K = 100；返回顺序即证据优先顺序；不得泄漏答案或金标。
- 参赛组别：开源方法榜（仓库公开、固定 commit、披露原始工作与方法改动）。

## 2. 接口契约（摘自官方 API Guide，登记为不可变基线）

### Add（同步写入）

```
POST {configured}/add
{
  "request_id": "eval:run_abc123:locomo_refined:conv-0:chunk-0",
  "messages": [{"role": "user", "timestamp": 1704067200000, "content": "memory text"}],
  "user_id": "eval:run_abc123:locomo:conv-0",
  "session_id": "eval:run_abc123:sample:0"
}
```

- `request_id` 必填；成功响应必须原样返回。
- `messages` 必填、按序；`role` 与非空 `content`；`timestamp` 可选（Unix 毫秒）。
- `user_id` 必填，是 Search 唯一检索范围；`session_id` 必填，仅用于组织。
- 同步语义：内容持久化且后续 Search 可立即检索后，才返回 `HTTP 200`。
- 响应：`{"success": true, "request_id": ..., "user_id": ..., "session_id": ...}`，
  三个 ID 与请求完全一致。
- 幂等：平台重试复用同一 `request_id` 与请求体，不得重复写入污染结果。

### Search

```
POST {configured}/search
{
  "query": "...",
  "options": ["A. ...", "B. ..."],   // 可选，选择题；不包含金标
  "user_id": "...",
  "top_k": 100
}
```

- 响应必须是 JSON 对象，顶层 `data` 为按相关性排序的数组：
  `[{"id", "content", "score"?, "created_at"?}]`。
- 数量不得超过 `top_k`；超量或非法条目判契约错误，不会静默截断。
- 只返回记忆证据，不得生成最终答案或把答案伪装成记忆。

### 错误与健康

- 业务错误推荐 `{"detail": {"reason": "..."}}`；字段校验失败 422 返回结构化明细。
- Health 通过无鉴权 GET 调用，任意 2xx 即正常。
- 鉴权支持 `Token` / `Bearer` / `X-Api-Key`；`none` 仅公开 smoke。

### 硬性隔离要求

- `user_id` 严格隔离：不同 user_id 的数据不得混合检索。
- Streaming：Add 与 Search 随事件推进交错发生；Search 只应看到当时已写入的数据。

## 3. 方案

### 3.1 总览

复用项目已有的 SQLite 存储层与 BM25 检索（`storage.py`），新增独立的
`src/memory_as_history/aml/` 适配模块：

```
aml/
  __init__.py
  contract.py        # 请求校验、响应组装、错误对象（AML 规范镜像）
  memory_service.py  # 按 user_id 的写入/检索服务：事务写入 + 幂等 + 隔离
  server.py          # 零依赖 HTTP 服务（stdlib ThreadingHTTPServer）
  __main__.py        # python -m memory_as_history.aml 启动入口
```

- 写路径：适配器自管事务，单连接单事务完成 `aml_add_log` 幂等标记 +
  memories 行 + 会话序号，杜绝部分写入与重试重复。
- 读路径：实例化项目 `Store`（同一 user DB），`recall(query, limit=top_k)`
  展平为 `data[]`；行结构完全复用项目 schema，测试保证两路一致。
- 零新增运行依赖：HTTP 用标准库，检索用项目自带 BM25；语义（hybrid）为
  可选开关（`AML_SEARCH_MODE=hybrid`），复用 `Store.search`。

### 3.2 user_id 隔离：按用户分库

- 存储根目录 `AML_DATA_DIR`（默认 `~/.memory-as-history/aml/`）。
- 每个 `user_id` 一个 SQLite 文件：`users/<sha256(user_id)[:32]>.db`。
- 检索时只打开该 user 的库；不同 user 物理隔离，跨 user 泄漏在构造上不可能。
- 映射为确定性哈希（user_id 含冒号等字符，不宜直接作文件名）；哈希映射
  保存在 `users/index.json`（或允许直接派生，无需额外表）。

### 3.3 Add：单事务写入与幂等

每个 Add 请求一个事务（`BEGIN IMMEDIATE`）：

1. 校验请求；`messages` 空或 `content` 空 → 422。
2. `aml_add_log(request_id TEXT PRIMARY KEY, user_id, session_id, status, at)`：
   - 已存在且 `status='done'` → 直接返回 200 回显（幂等命中，不重写）。
   - 已存在且 `status='failed'` → 覆盖重试（旧事务已回滚，无残留）。
   - 不存在 → 插入 `pending`。
3. 逐条消息写入 `memories` 表（列与 `Store.remember` 完全一致），
   `session_position` 由 `aml_sessions(session_id, next_position)` 在
   同一事务内推进，保证同会话全局有序、可重入。
4. `status='done'`，COMMIT。
5. 失败 → ROLLBACK，`status='failed'`，按规范返回 5xx/422。

事务原子性使重试成为幂等操作：失败即整体回滚，不存在部分写入。

### 3.4 Search：证据优先级展平

```
Store(user_db).recall(query, limit=top_k)
→ 展平顺序：anchors（身份锚点）> canon（任务 canon）> memories（BM25 排序）
→ data[]：{id, content, score?, created_at?}
```

- `score`：memories 行取 `relevance`（BM25）；anchors/canon 无相关性分时省略
  score 字段（契约中可选），仅以顺序表达优先级。
- `created_at`：项目 ISO 时间戳规范化为 `Z` 格式（对齐官方示例）。
- 数量硬性 ≤ top_k（anchors 例外规则不适用于评测场景——评测流不产生锚点；
  若未来出现锚点，仍以全局预算裁剪，超量即内部错误）。
- 无答案泄漏：只回传已存记忆内容；选择题 options 不参与检索，仅透传。
- 遗忘语义（项目核心）：`forgotten_at` 非空的行由 recall 天然排除；
  评测流不调用 forget，该机制在治理类数据集上作为可选项后续接入。

### 3.5 Streaming 持续记忆

- Add 增量到达：每次 Add 独立同步持久化，Search 查询天然只含已提交数据。
- 同会话顺序：`session_position` 全局递增，供时间线/邻接类证据使用。
- 更新/冲突/失效：v0.1 不做启发式覆盖（忠实存储全部事实，保证事实召回）；
  治理类增强（更新检测、冲突保留、过期抑制）作为 v0.2 候选，
  在拿到私有/公开数据形态后再定，避免无数据支撑的拍脑袋规则。

### 3.6 检索策略

- 默认 `lexical`：项目 BM25（CJK 双字 + 拉丁词），无模型、无外网依赖，
  已在 LoCoMo 外部数据上 42.76% 证据召回（与独立 BM25 持平）。
- 可选 `hybrid`：`AML_SEARCH_MODE=hybrid` 复用 `Store.search`（本地 E5，
  召回 51.90%）；需首次下载模型（约 1GB+），部署时权衡内存。
- 官方文本数据集（LoCoMo-Refined 等）与项目评测数据同源，
  但官方按统一 Answer 流程打分（非证据 Recall@5），以实测为准。

### 3.7 鉴权与健康检查

- `AML_API_KEY` 为空 → 无鉴权（仅用于本地 smoke）；非空 → 要求
  `Authorization: Bearer <key>` 或 `X-Api-Key: <key>`（403/401 按规范）。
- `GET /health` 无鉴权，返回 200 `{"status": "ok"}`。

## 4. 测试与验证计划

- `tests/test_aml_adapter.py`：契约测试（直接调用 memory_service 层）——
  往返可见性、幂等（同 request_id 重放不重复）、user 隔离、
  top_k 边界、空结果、格式错误 422、Streaming 交错可见性。
- `tests/test_aml_http.py`：真实 HTTP 层（ThreadingHTTPServer 起服务）——
  路由、鉴权、健康检查、请求/响应结构。
- `scripts/aml_smoke.py`：模拟官方 smoke 的端到端脚本
  （Add → Search → 断言格式与数量），CI 可跑、无模型依赖。
- 本地手动验证：`python -m memory_as_history.aml` + curl 一轮。

## 5. 交付物与参赛材料（后续任务）

- Dockerfile + 部署文档（服务器建议：香港/新加坡轻量 VPS，Let's Encrypt）。
- 参赛材料清单：系统名与版本、联系人、开源方法披露（固定 commit、
  原始工作与方法改动——本适配层复用项目自身存储/检索，新增为 HTTP 适配，
  需在提交说明中如实披露）、容量声明、部署说明。
- 时间线：10 月中旬前完成首次 Full。

## 6. 明确的边界（不做）

- 不修改 `storage.py` / `server.py` 核心 schema 与契约（保持 419 测试基线）。
- 不实现代码/多模态赛道。
- 不做无数据支撑的治理启发式；v0.1 忠实存储 + BM25。
- 不把 Eval Key / Memory System Key 写入仓库。

## 7. v0.2 登记：确定性记忆治理（2026-10-01 实现）

> 设计稿原 3.5 节将「更新检测、冲突保留、过期抑制」列为 v0.2 候选。
> 结合 Cycle 1 排行榜分析（MemoraX 治理维度拉开差距、确定性冲突解决
> 配方在 MemoryAgentBench FactConsolidation 达 SOTA）与审核窗口期，
> 于 2026-10-01 落地为 `src/memory_as_history/aml/governance.py`，
> 以下为偏差登记。

### 7.1 机制（全部确定性、无模型、可复现、可审计）

1. **写时更新检测（Add）**：每条消息写入后，与同 user 最近 300 条记忆
   比较。满足「新版显著更长（≥1.15×）」且「旧 token 完全包含于新表述」
   （或 Jaccard ≥ 0.6）时，在 `aml_updates` 表记录
   `(memory_id, superseded_by, similarity)` 边，并写 `audit_log`
   （`aml_supersede`）。不删除、不改写任何既有行。
2. **读时版本抑制（Search）**：展平后的证据窗口内：
   - 内容归一化相等（仅空白/大小写差异）→ 去重保留新版（`aml_dedup_merge`）；
   - 判定为同一事实的多版本 → 旧版 score ×0.25（强信号）/×0.5（弱信号）
     （`aml_version_discount`），**旧版仍留在结果中**（历史类问题可检索），
     仅排名后移。
   - 治理后按新分数稳定重排：无 score 条目（anchors/canon）保持最高优先级，
     有 score 条目降序。
3. **去重只认内容实质相同**：`_tokenize` 丢弃单字符数字（"第8条"与"第9条"
   token 相同但事实不同），因此去重判定使用归一化内容相等，绝不用 token
   相似度。此规则由 `test_search_respects_top_k` 回归测试强制。

### 7.2 与核心 schema 的边界

- 仅新增 AML 自有表 `aml_updates`（前缀 `aml_`），`SCHEMA`/`KNOWLEDGE_SCHEMA`
  未动；`storage.py`/`server.py` 零改动。
- Search 响应保持契约纯净：每条目仍为 `{id, content, score?, created_at?}`，
  治理决策只写 `audit_log`，不进响应。

### 7.3 性能

- 写时：每消息与最近 300 行比较（token 集合运算，毫秒级）。
- 读时：O(n²) 仅在返回窗口（≤ 官方 Top K=100）内，实测 100 条内 <1ms 量级。

### 7.4 测试

- 新增 `tests/test_aml_governance.py`（14 项）：相似度信号、写时边记录与
  审计、时间方向（旧不能 supersede 新）、幂等重放、读时降权与去重、
  历史保留、响应契约纯净、等长模板条目不误判。
- 全量回归：`pytest tests/`（原 455 + 新增 14 = 469 全绿）。

## 8. v0.3 邻轮证据扩展（P0，2026-10-01）

追榜诊断：LoCoMo 自测 1,527 题中**多轮证据题（405 题）召回仅 17.02%**，
单轮证据题（1,122 题）52.05%；完美选择器上限 99.50% 证明预算不背锅，
多证据覆盖率是最大短板。会话内邻轮扩展直接补这块。

### 8.1 机制（读时、确定性、有界）

- `MemoryService._expand_neighbors`：对每个排序种子（recall 命中的
  记忆），在同 `session_id` 内取 `session_position ∈ [pos-K, pos+K]`
  （K 默认 3，环境变量 `AML_EXPAND_RADIUS` 可调）的未遗忘轮次。
- **相关性门控**：扩展轮必须与「query token ∪ 全部种子 token」至少
  共享 1 个 token（`signal` 并集）。理由：多证据关键轮常不含 query
  词（问"住在哪"、答"后来搬到上海"），但必与命中轮谈同一主题；
  完全不重叠的轮次是填充噪声，不得进入证据窗。
- **排序**：扩展轮 score = token 命中数 × 0.01——恒小于 BM25 种子分，
  保证扩展证据排在种子之后、不干扰主排序；`govern_entries` 稳定重排后
  仍如此。
- **预算**：扩展上限 = `top_k - 种子数`；总体再经 `data[:top_k]` 截断，
  Top K 是全局硬边界。
- **审计**：每条扩展写 `audit_log`（action `aml_turn_expand`）。
- **去重**：扩展候选按 `(session_id, session_position)` 去重，且排除
  已在种子集合中的 id。

### 8.2 事务修复（v0.2 遗留）

Store 连接为手动事务模式（`sqlite3.connect` 默认 `isolation_level=""`），
读时审计（`govern_entries` 内 `_audit`）此前未 commit，连接关闭即回滚。
修复：`govern_entries` 两个出口（含 `n<2` 提前返回）在 `conn` 非空时
`conn.commit()`——同时提交同连接上更早的扩展审计，一次读路径一事务。

### 8.3 测试

- 新增 `tests/test_aml_expansion.py`（5 项）：邻轮证据被扩展且填充轮
  被排除、token 门控、Top K 预算、契约纯净（`{id,content,score?,
  created_at?}`）、扩展审计落库。
- 全量回归：`pytest tests/` = **474 passed**（469 + 5，零回归）。

### 8.4 已知边界

- 扩展只取同会话 ±K 轮，不跨会话、不扩展锚点/规范条目（评测流无）。
- token 门控是词法启发式：语义改写但与种子零共享 token 的轮次仍会
  漏掉——留给 P1 hybrid 的语义腿覆盖。

## 9. v0.4 hybrid 检索（P1，2026-10-02）

LoCoMo 自测基线：lexical mean recall@5 **42.76%**；同一数据跑 hybrid
（E5-small + RRF，无过滤）**51.90%**（+9.14pp）。P1 把 hybrid 正式接入
AML 层并上线。

### 9.1 语义栈（项目已有，`semantic.py`）

- `LocalE5`：pinned `intfloat/multilingual-e5-small`
  （revision `614241f6…`，CPU，safetensors，512 tokens），默认
  `local_files_only`，显式 `python -m memory_as_history.semantic download`
  下载；查询/文档分别加 `query: `/`passage: ` 前缀，归一化余弦。
- `rank_candidates`：RRF 融合（`RRF_K=60`），输出每行
  `semantic_similarity` + `search_score`（hybrid 下 = BM25 排名腿 +
  语义排名腿）。
- `Store.search(mode='hybrid'|'semantic')`：惰性加载模型、事务外推理、
  新鲜 eligibility 复核。

### 9.2 AML 层接入

- `MemoryService.search()`：lexical 走 `store.recall`（原路径）；hybrid/
  semantic 走 `store.search`。契约 score 字段：lexical 用 BM25
  `relevance`，hybrid/semantic 用 RRF `search_score`（`_entry` 增加
  `score_key` 参数）。
- **语义门控（关键）**：纯语义命中（`relevance=0`）要求
  `semantic_similarity ≥ semantic_min`（默认 **0.85**）才算证据；词法
  命中（BM25>0）无条件保留。E5-multilingual 中文短句相似度整体偏高
  （实测无关 0.79–0.85、相关 0.90+），0.30 等宽松阈值会把无关行全量
  放行、污染答案生成；0.85 让"完全无关"query 返回空。`semantic_min`
  经 `--semantic-min` / `AML_SEMANTIC_MIN` 可调。
- **进程级模型共享**：`LocalE5._shared_model` 类级缓存——AML 服务按
  请求建 Store，若每次 search 重新加载 470MB 模型（实测 ~60s）会打爆
  smoke 超时；共享后首请求加载、后续 <0.1s。

### 9.3 部署

- Dockerfile 启用 `.[semantic]` extra，构建期预下载 pinned 模型（镜像
  自带，运行零外网依赖）。
- 服务器（2C2G）加 2G swap（`/swapfile`，fstab 持久化）以容纳 torch
  推理峰值。
- compose `AML_SEARCH_MODE=hybrid`；`AML_SEMANTIC_MIN=0.85`（默认）。
- 公网验证：同义改写 query 精确召回对应记忆；完全无关 query 返回空；
  首次 search ~60s（加载），其后 <1s。

### 9.4 测试与回归

- 新增 `tests/test_aml_hybrid.py`（9 项，mock `Store.search`，不加载
  模型）：语义独有命中保留/丢弃、词法命中不受门控、契约 score 用
  RRF 分、高门槛过滤、零分丢弃、lexical 回归、semantic 模式、参数
  校验。
- 全量回归：`pytest tests/` = **489 passed**（474 + 15，零回归）。

## 10. v0.5 时间线索检索（P2，2026-10-02）

memory-as-history 的立身之本：**记忆是历史，不是快照**。检索带时间的
问题（"她去年住哪"）时应返回**那个时刻的证据状态**，而非最新真相。
P2 把这一特色正式接入 AML 层：显式时间线索触发、时间窗内证据加权。

### 10.1 机制（读时、保守、可开关、可审计）

1. **时间表达检测（`aml/temporal.py`）**：只识别**显式相对时间词**
   （上周/上个月/去年/昨天/几天前 + last week/a year ago 等），带
   span 去重（更具体规则先占位）。**故意不识别**模糊词（最近/当时/
   recently）——它们是日常问题的高频词，误触发会污染排序。
   每条线索 = `TemporalClue(label, offset_seconds, half_window_seconds)`。
2. **时间锚定**：`now` = 召回证据窗内最新 `created_at`（会话末端即
   "现在"）；时间窗中心 = `now - offset`。
3. **保守重排**：命中的行按 `time_weight` 乘子调整契约 score——
   窗内 ×1.25、窗外 ×0.8（默认；可调）。乘子小，误触发不会翻盘。
   多条线索取最强。
4. **审计**：触发时写 `audit_log`（action `aml_temporal_hit`，
   reason 含 label/offset）；审计失败绝不影响检索。
5. **开关**：`--use-temporal` / `AML_USE_TEMPORAL`（默认 true）。

### 10.2 与既有链的关系

- 时序在 `govern_entries` **之前**执行：先时间重排，再治理去重/版本
  抑制，最后全局 Top K 截断。
- 不新增 schema；复用 `created_at`（已有列）。
- 历史版本重放（supersede 链旧版在时间窗语义下提权）列为 P2.5 候选：
  需真实评测 query 分布佐证后再接线，避免无数据支撑的启发式。

### 10.3 测试与回归

- 新增 `tests/test_aml_temporal.py`（16 项）：中英文检测、模糊词不
  触发、span 去重、窗内/窗外乘子、ISO 解析、集成（时间 query 窗内行
  提升、普通 query 不受影响、开关关闭不动分）。
- AML 全部 6 个测试文件 = **69 passed**；全量 `pytest tests/` 490
  passed（7 个 `test_semantic_search.py` 用例因该文件内部
  monkeypatch/import 顺序敏感失败，与 P2 无关，单独跑均通过）。

### 10.4 已知边界

- 绝对日期（"3月5日"）暂不识别；相对时间到绝对日历的对齐留后续。
- 权重保守：对"证据全在窗内"的问题提升有限，但不引入误伤。

## 11. v0.6 原典分块（P3，2026-10-03）

"史料保真"落到存储底层：长消息不再整体入库后被 512-token 语义截断
或 BM25 稀释，而是在**句子边界**切成完整块，共享原消息元数据，
原文可按块序完整重建。普通短消息（LoCoMo 主流形态）完全不受影响。

### 11.1 机制（写时、确定性、保真优先）

1. **触发阈值**：仅 `len(content) > 1200` 字符才分块（≈检索上限）；
   短消息原样入库（零变化、零回归）。
2. **保真切割**（`aml/chunking.py`）：按 `。！？；.!?\n` 句子边界切
   （lookbehind 保留分隔符；`\n` 单独保留，原文可逐字重建）；每块
   ≥ `min_chars=200`；**无句边界的超长句不硬切**（保真 > 收益）。
3. **元数据共享**：子块共享 `event_at`/`session_id`；`session_position`
   连续递增（子块位置相邻，邻轮扩展窗口自然覆盖全块）。
4. **位置账本**：`aml_sessions.next_position` 从 `+len(messages)` 改为
   `+实际写入行数`（分块后不重叠、不覆盖）。
5. **治理适配**：`record_possible_updates` 只在**消息第一块**执行，且
   用**完整原文**比较（子块不参与 supersede 判定，避免碎片误判）。

### 11.2 检索表现

- 长消息的每个语义段独立可命中（BM25 词法 + E5 语义都按块召回）；
- Top K 预算下长消息从"1 条挤占"变为"多块按需入选"；
- 响应 content 为完整句块（可读、可溯源到原消息）。

### 11.3 测试与回归

- 新增 `tests/test_aml_chunking.py`（10 项）：短消息不动、句子边界
  切分可重建、min 尺寸、无边界保真、换行保留、元数据共享、position
  连续、跨批不重叠、按块检索命中。
- AML 全部 7 个测试文件 = **79 passed**（69 + 10，零回归）。

### 11.4 已知边界

- 分块是写时策略；已入库的旧长消息不回溯重切（评测流为一次性写入，
  无影响）。
- 阈值与 min 尺寸可调（常量，未暴露 env——评测数据形态确定后如需
  微调再暴露）。
