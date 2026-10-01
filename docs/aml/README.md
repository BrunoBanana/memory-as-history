# AML 参赛部署手册（文本赛道 · 开源方法榜）

> Agent Memory Leaderboard 2026 第二期参赛接口的部署、验证与提交材料清单。
> 设计文档见 [docs/plans/2026-09-30-aml-adapter.md](../plans/2026-09-30-aml-adapter.md)。

## 1. 这是什么

`memory-as-history` 的 AML 适配层：

- `POST /add`：按官方契约同步写入记忆（`request_id` / `messages` / `user_id` / `session_id`），单事务幂等。
- `POST /search`：按 `user_id` 检索，返回 `{"data": [...]}` 证据数组（≤ `top_k`，顺序即证据优先级）。
- `GET /health`：健康检查（任意 2xx 即正常）。

实现零新增运行依赖：HTTP 为标准库，检索复用项目自带 BM25；可选
`AML_SEARCH_MODE=hybrid` 开启本地 E5 语义检索（首次使用需下载模型）。

## 2. 本地运行与验证

```bash
pip install -e ".[test]"
python scripts/aml_smoke.py            # 端到端模拟官方 smoke（无模型依赖）
python -m memory_as_history.aml --port 8000
```

自测：`python -m pytest tests/test_aml_adapter.py tests/test_aml_http.py -v`
（26 个契约用例，覆盖幂等、隔离、top_k、Streaming 交错可见性等）。
完整套件 `pytest tests/ -q` 454 通过（含原有 419）。

## 3. 服务器选择建议

**硬约束**：平台只连接公网可达的 HTTPS 接口；大陆境内服务器提供 Web 服务
需要 ICP 备案（通常数周），赶不上 10/31 截止。推荐**免备案**方案：

| 方案 | 规格建议 | 参考价格 | 说明 |
| --- | --- | --- | --- |
| 腾讯云 / 阿里云 香港轻量应用服务器 | 2C2G，30-40GB | 约 ¥40-60/月 | 国内与海外均可达，网络延迟低 |
| DigitalOcean / Vultr 新加坡 | 1C2G（$12/月）或 2C4G | 约 $12-24/月 | 全球可达，IP 干净 |
| 备选：Hetzner（欧洲） | CX22 起 | 约 €4-8/月 | 便宜但离评测调度可能更远 |

- 仅词法（BM25）：1-2GB 内存足够。
- 开启 hybrid：建议 2-4GB（E5 模型约 1GB+，首次启动下载）。
- 需要一个域名（约 ¥50-70/年）用于 HTTPS；用 Caddy 自动签发
  Let's Encrypt 证书（见下方部署）。

## 4. Docker 部署

```bash
docker build -t memory-as-history-aml .
docker run -d --name aml \
  -p 8000:8000 \
  -v aml-data:/data \
  -e AML_API_KEY=<你的 Memory System Key> \
  memory-as-history-aml
```

HTTPS（推荐 Caddy 反代，自动证书）：

```bash
cp docker-compose.example.yml docker-compose.yml
# 编辑 Caddyfile：把 aml.example.com 换成你的域名
docker compose up -d
```

验证：`curl https://你的域名/health` 返回 2xx 即部署成功。

## 5. 鉴权

- `AML_API_KEY` 留空：无鉴权（**仅限本地/公开 smoke 阶段**）。
- 正式提交：设置 `AML_API_KEY`，平台用 `Authorization: Bearer <key>`
  或 `X-Api-Key: <key>` 调用。
- **不要把 Eval Key / Memory System Key 提交进仓库**。

## 6. 参赛材料清单（10/31 23:59 前提交）

### 共同材料
- [ ] 系统名称与版本：`memory-as-history`（AML adapter，固定 commit 后登记）
- [ ] 联系人、机构/团队
- [ ] 拟参评类型：文本记忆；组别：开源方法榜
- [ ] 方法/产品说明（可直接引用本手册与设计文档）
- [ ] 允许公开展示的信息
- [ ] 完整提交说明

### 开源方法榜附加
- [ ] 已部署的 Add/Search API 地址（HTTPS）+ 鉴权方式 + 容量声明
- [ ] 公开仓库：https://github.com/BrunoBanana/memory-as-history
- [ ] 固定 commit（提交准入前 `git rev-parse HEAD` 记录）
- [ ] 原始工作与方法改动披露：
     本项目为个人原创工作；适配层复用项目自身存储与 BM25 检索，
     未复制第三方实现。记忆学框架（Halbwachs/Nora/Assmann/Ricoeur）
     为设计思想来源，已在 README 与 reading report 中注明。
     检索为**无模型确定性实现**（BM25，CJK 双字 + 拉丁词），
     不使用比 gpt-4o-mini 更强的模型，符合开源方法榜 Add 模型约束。

### Full 前置清单（提交 full 时逐项勾选）
- [ ] Smoke 已通过
- [ ] API 契约正确（本仓库契约测试 + aml_smoke.py 验证）
- [ ] Add/Search 模型规则已理解
- [ ] Full 容量已确认（存储、带宽、并发声明与实测一致）
- [ ] 部署信息完整（版本、端点、鉴权、容量、运行限制）
- [ ] 原创披露已完成
- [ ] 实质性提交（非重复低质量提交）
- [ ] 无操纵/作弊（无硬编码、无泄漏、无注入、无刷榜）

## 7. 时间线（截至 2026-09-30）

| 事项 | 目标时间 |
| --- | --- |
| 本地接入层 + 契约验证 | 已完成（454 测试全绿） |
| 服务器购买 + 域名 + HTTPS 部署 | 建议 10/05 前 |
| 提交 Evaluation Access Request | 服务器部署完成后立即 |
| 官方 Smoke | 审核通过后当天 |
| 按 smoke/私有结果调优检索 | Smoke 后 1-3 天 |
| **第一次 Full** | **目标 10/15 前**（留足排队与重试余量） |

## 8. 关键风险与说明

- 第二次 Full 需距首次完成 30 天，11/04 评测停止前基本不可用 →
  **按一次高质量 Full 规划**。
- 官方按统一 Answer 流程打分（非证据 Recall@5）；本项目 LoCoMo 证据召回
  42.76%（hybrid 51.90%）是检索质量参考，不代表官方得分。
- 文本赛道含 Streaming：本适配层天然支持（每次 Add 同步持久化，
  Search 只读已提交数据）；更新/冲突/失效治理为 v0.2 候选，
  待取得数据形态后迭代，不在无数据支撑时拍脑袋加规则。
