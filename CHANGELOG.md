# 版本记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## 1.2.0 — 2026-11-20

### 新增

- **多轮对话记忆**：`/api/ask` 自动携带同一会话最近 `HISTORY_TURNS` 轮问答（默认 4 轮），可用 `history=false` 关闭；新增 `HISTORY_TURNS` 配置项。
- **SSE 流式回答**：新增 `POST /api/ask/stream`，按 `sources` → `token`* → `done` 顺序推送事件，异常时推送 `error`；前端改为边接收边渲染，并展示打字光标。
- **混合检索**：`strategy=hybrid`，将向量相似度与中英文关键词重合度加权融合（`HYBRID_VECTOR_WEIGHT`，默认 0.7），可把含订单号、政策编号的片段排到前面。
- **检索可调参数**：`k` 支持按请求指定并在 1–20 间收敛；`RETRIEVAL_MIN_SCORE` 提供最低相关度阈值；重复片段按来源 + 页码 + 内容哈希去重。
- **内容去重**：上传时计算 SHA-256，同知识库内相同内容返回 `status=duplicate`，不再重复建索引。
- **重新索引**：新增 `POST /api/knowledge-bases/{kb_id}/documents/{document_id}/reindex`，失败文档可用原始文件重建索引；前端在失败行显示“重新索引”按钮。
- **知识库管理**：新增 `GET/PATCH /api/knowledge-bases/{kb_id}`、`GET .../stats`、`GET .../documents/{document_id}`（含流水线明细）。
- **会话重命名**：新增 `PATCH /api/conversations/{conversation_id}`。
- **分页**：知识库、文档、会话、消息、流水线列表支持 `limit` / `offset`，并返回 `X-Total-Count`、`X-Page-Limit`、`X-Page-Offset`、`X-Has-More`。

### 改进

- **数据层健壮性**：SQLite 启用 WAL、`busy_timeout` 与 `synchronous=NORMAL`；新增 6 个查询索引；引入 `PRAGMA user_version` 增量迁移，旧库启动时原地补列且保留数据。
- **写入回滚**：`ingest_file` 在向量写入失败时清理该文档的残留向量，避免出现无主片段。
- **可观测性**：请求 ID 与 `X-Process-Time-Ms` 中间件、未捕获异常统一 JSON 响应、`/api/health` 探活数据库并返回版本与规模。
- **提示词结构**：检索上下文放入 system message，历史问答按角色注入，用户问题保持独立，便于模型区分参考资料与对话。
- **前端**：策略选择新增混合检索；上传结果提示去重情况；健康状态显示 provider 与版本。

### 测试与文档

- 新增 `test_conversation_api.py`、`test_document_management_api.py`、`test_retrieval_strategies.py`、`test_store_migrations.py`，用例数从 6 增至 29。
- README 更新架构图、工作量表、API 表、配置表与已知边界；README 与新增本文件同步。

### 兼容性

- 升级后首次启动会自动迁移既有数据库，无需手动删除 `data/`。
- 原有 `POST /api/ask`、`/api/evaluate`、批量评测接口的请求与响应字段保持兼容（仅新增字段）。

## 1.1.0

- 引入 MMR 检索、批量评测（Precision@K / Recall@K / MRR / 延迟）、流水线记录与前端评测工作台。
- 拆分 FastAPI 路由、补充 Docker Compose 与 GitHub Actions。

## 1.0.0

- 首个版本：知识库 CRUD、多格式文档上传、Chroma 向量索引、引用溯源问答与 SQLite 持久化。
