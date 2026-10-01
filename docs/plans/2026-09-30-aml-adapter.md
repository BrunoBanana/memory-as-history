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
