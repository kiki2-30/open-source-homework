# 知问：多格式智能知识库问答平台

一个基于 **LangChain** 的全栈 RAG（检索增强生成）应用。用户可以创建多个知识库，上传 PDF、Word、Markdown、TXT 资料，自动解析和切分文本、生成向量索引，并获得带有原文片段与文档来源的问答结果。支持 Ollama 本地模型与 OpenAI 兼容模型。

> **GitHub 开源仓库：[kiki2-30/open-source-homework](https://github.com/kiki2-30/open-source-homework)**

## 功能一览

- **知识库管理**：创建、重命名、切换、删除知识库；各知识库文档、向量索引和聊天记录隔离，并提供文档块数、会话数、失败任务等统计。
- **多格式资料导入**：支持 `.pdf`、`.docx`、`.md`、`.markdown`、`.txt`，每份文件最大 25 MB；按内容 SHA-256 去重，重复上传不会重复建索引。
- **文档处理流水线**：解析文档、递归文本切分、保存元数据、调用 Embeddings 并写入 Chroma；写入失败会回滚半成品向量，失败文档可一键重新索引。
- **RAG 问答**：LangChain 检索 + 对话模型回答；提示模型只使用检索上下文作答，资料不足时说明。
- **多轮对话记忆**：同一会话内自动携带最近 `HISTORY_TURNS` 轮问答，追问代词和省略表达能被正确理解。
- **SSE 流式回答**：`/api/ask/stream` 逐 token 推送，前端边接收边渲染，并单独下发来源事件。
- **三种检索策略**：相似度 Top-K、MMR 多样性检索，以及融合向量分数与中英文关键词重合度的混合检索。
- **引用溯源**：展示引用编号、文件名、PDF 页码（若适用）、相关原文片段和相似度分数。
- **聊天记录**：按知识库持久化多轮问题、回答和引用，支持会话重命名、历史分页浏览和删除。
- **评测工作台**：单题调试或导入 JSON / JSONL 批量评测集；统计答案命中率、来源命中率、Precision@K、Recall@K、MRR 和平均延迟，保留逐题结果和历史批次。
- **工程化保障**：WAL 模式 + 索引 + 可迁移 schema、分页响应头、请求 ID 与耗时中间件、健康检查探活数据库；Docker Compose 一键启动，GitHub Actions 双 Python 版本（3.11 / 3.13）+ 前端检查跑测试。
- **模块化后端**：按知识库、会话、操作和评测职责拆分 FastAPI 路由；Python Ruff 和前端 Prettier 检查接入 CI。
- **轻量部署**：FastAPI 提供 REST API 与浏览器 UI；SQLite 存储管理数据，Chroma 持久化向量索引。

## 技术架构

```text
浏览器（原生 HTML / CSS / JavaScript）
        │ REST / multipart / SSE
        ▼
FastAPI（请求 ID + 耗时中间件、分页、统一异常）
   │
   ├── SQLite（WAL + 外键 + 索引 + schema 迁移）
   ├── LangChain 文档解析与 RecursiveCharacterTextSplitter
   ├── LangChain Embeddings ── Ollama / OpenAI 兼容 API
   ├── Chroma 向量库（按知识库隔离、持久化）
   ├── 检索层（similarity / MMR / hybrid + 分数阈值 + 去重）
   ├── LangChain Chat Model（检索上下文 + 会话历史 + 来源编号提示词）
   └── 批量评测服务（回答质量 + 来源排序 + 延迟指标）
```

### 项目结构

```text
app/
├── api/
│   ├── common.py            # 分页参数、分页响应头与资源校验
│   ├── conversations.py     # 问答、SSE 流式回答、会话历史与重命名
│   ├── evaluations.py       # 批量评测 API 与指标聚合
│   ├── knowledge_bases.py   # 知识库、资料生命周期、去重与重新索引
│   └── operations.py        # 流水线与单题评测 API
├── static/
│   ├── app.js               # 浏览器交互、流式渲染、资料管理与评测工作台
│   ├── index.html           # 单页应用结构
│   └── style.css            # 响应式工作台样式
├── knowledge.py             # 文档解析、切分、检索策略与流式生成
├── main.py                  # 应用生命周期、中间件、异常处理与健康检查
└── store.py                 # SQLite schema、迁移、连接管理与统计查询
tests/
├── test_batch_evaluation_api.py     # 批量评测、指标与失败部分结果
├── test_conversation_api.py         # 多轮记忆、SSE 流式、重命名、分页
├── test_document_management_api.py  # 去重、重新索引、统计与 404 分支
├── test_rag_api.py                  # 上传、相似度/MMR 问答、引用与生命周期
├── test_retrieval_strategies.py     # 混合检索、分数阈值、去重与提示词结构
├── test_store.py                    # 数据库初始化、级联删除与持久化
└── test_store_migrations.py         # 旧库升级、WAL 与统计聚合
examples/
├── documents/                   # 可直接上传的示例知识文档
└── evaluation/                  # 与文档配套的 JSONL 回归集
```

## 项目实现工作量

| 子系统 | 主要实现 | 可验证结果 |
|---|---|---|
| 文件摄取 | PDF 按页抽取、DOCX/文本解析、递归切分、元数据保留、Chroma 持久化、失败状态记录、写入失败回滚、SHA-256 内容去重、失败重试索引 | 重复上传同一文件返回 `duplicate` 且不重复建索引；可对失败文档执行 `reindex` |
| 检索增强 | 按知识库隔离 collection、similarity / MMR / hybrid 三种策略、重排前候选数、相似度阈值过滤、同源重复片段去重、`k` 边界校验 | 同一问题切换策略可对比排名与分数，混合检索能把含关键编号的片段排到前面 |
| 会话与生成 | 多轮历史注入（最近 `HISTORY_TURNS` 轮）、引用编号提示词、来源片段返回、SSE 逐 token 流式输出、失败写入流水线 | `/api/ask/stream` 依次下发 `sources` / `token` / `done` 事件，追问可复用上下文 |
| 后端 API | 按知识库、会话、操作、评测拆分路由；`PATCH` 重命名、统计概览、文档详情、分页与 `X-Total-Count` 响应头、级联清理 | OpenAPI 文档包含可运行的 REST 接口与分页参数 |
| 数据持久化 | SQLite 管理知识库、文档、会话、消息、流水线和评测批次；WAL + busy timeout、外键级联、8 个查询索引、`PRAGMA user_version` schema 迁移 | 旧版本数据库启动时原地升级，重启后可读历史会话和评测批次 |
| 评测系统 | JSON / JSONL 数据集导入，逐题隔离失败，汇总答案命中、来源命中、Precision@K、Recall@K、MRR、延迟 | 附 5 题可运行示例回归集 |
| 全栈界面 | 响应式聊天、流式打字机效果、来源卡片、资料管理（去重提示 + 重新索引）、流水线、策略选择与批次历史 | 浏览器端完成从导入到查看评测的操作 |
| 可观测性 | 请求 ID、`X-Process-Time-Ms`、统一异常 JSON、健康检查探活数据库并返回版本与规模 | 任意响应都带 trace 头，`/api/health` 可判断存储是否可用 |
| 工程交付 | Docker Compose、环境变量配置、29 个单元/集成测试、Ruff + Prettier 检查、多版本 GitHub Actions | 本地 `unittest` 全绿，CI 配置与容器启动说明齐备 |

### 核心流程

1. 上传文件并按扩展名选择 PDF / DOCX / UTF-8 文本解析器；同一知识库内按 SHA-256 判定重复资料。
2. 以 `CHUNK_SIZE`、`CHUNK_OVERLAP` 配置分块，保留文件名、知识库 ID、文档 ID、页码等元数据。
3. 通过配置的 Embeddings 生成向量并写入对应知识库的 Chroma collection；写入异常时回滚该文档的向量。
4. 用户提问后选择相似度 / MMR / 混合检索，取 Top-K 片段（可配置最低分数），并对同源重复片段去重。
5. 把片段编号、最近若干轮问答历史与当前问题组成提示词，模型输出带 `[1]` 形式的来源标注。
6. 回答、引用片段、引用文件与会话记录写入 SQLite；流式接口在生成过程中实时下发 token。

## 快速开始

需要 Python 3.11+。安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 方案 A：Ollama 本地模型

安装并启动 [Ollama](https://ollama.com/)，下载聊天模型及嵌入模型：

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

`.env` 默认使用 Ollama。按机器配置调整 `OLLAMA_CHAT_MODEL`；两种模型均需在 Ollama 中可用。

### 方案 B：OpenAI 或兼容服务

修改 `.env`：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=你的密钥
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# 可选：兼容服务地址
# OPENAI_BASE_URL=https://your-compatible-endpoint/v1
```

聊天与嵌入均使用同一个 provider 配置。密钥只放在本地 `.env`，不要提交到公开仓库。

### 启动

```bash
uvicorn app.main:app --reload --env-file .env
```

打开 <http://127.0.0.1:8000> 使用页面，或打开 <http://127.0.0.1:8000/docs> 调试 API。

首次上传时，应用会在 `data/` 下创建 SQLite 数据库、上传文件和 Chroma 索引。上传需要 Embeddings 服务可连接；问答还需要聊天模型服务可用。

### 五分钟功能演示

1. 创建名为“退款政策”的知识库。
2. 上传 [`refund-policy.md`](./examples/documents/refund-policy.md)。
3. 打开“问答评测”，导入 [`refund-policy.jsonl`](./examples/evaluation/refund-policy.jsonl)。
4. 运行相似度评测，再切换 MMR 比较回答命中率、来源 Recall@K、MRR 和延迟。

### Docker Compose

复制 `.env.example` 为 `.env`，再启动应用和 Ollama：

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
docker compose exec ollama ollama pull nomic-embed-text
```

应用与 Ollama 模型分别存入 `knowledge-data`、`ollama-models` Docker volume。页面地址仍为 <http://127.0.0.1:8000>。首次下载模型需要网络和足够磁盘空间。

## API 概览

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/api/health` | 健康检查：provider、版本、数据库状态与规模 |
| `GET`, `POST` | `/api/knowledge-bases` | 分页列出 / 创建知识库 |
| `GET`, `PATCH`, `DELETE` | `/api/knowledge-bases/{kb_id}` | 读取详情 / 重命名 / 删除知识库及关联数据 |
| `GET` | `/api/knowledge-bases/{kb_id}/stats` | 文档、文本块、会话、失败任务等统计 |
| `GET`, `POST` | `/api/knowledge-bases/{kb_id}/documents` | 分页列出（可按 `status` 过滤）/ 上传资料 |
| `GET`, `DELETE` | `/api/knowledge-bases/{kb_id}/documents/{document_id}` | 查看文档详情与流水线 / 删除文档和向量 |
| `POST` | `/api/knowledge-bases/{kb_id}/documents/{document_id}/reindex` | 用原始文件重新解析并重建索引 |
| `POST` | `/api/ask` | 提问并返回回答、引用、会话 ID 与耗时 |
| `POST` | `/api/ask/stream` | 以 SSE 流式返回 `sources` / `token` / `done` / `error` |
| `GET` | `/api/knowledge-bases/{kb_id}/conversations` | 分页查看会话列表 |
| `PATCH`, `DELETE` | `/api/conversations/{conversation_id}` | 重命名 / 删除会话 |
| `GET` | `/api/conversations/{conversation_id}/messages` | 分页查看会话消息 |
| `GET` | `/api/knowledge-bases/{kb_id}/pipeline` | 分页查看处理流水线（可按 `status` 过滤） |
| `POST` | `/api/evaluate` | 执行一次问答评测 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations` | 查看评测历史与汇总 |
| `POST` | `/api/knowledge-bases/{kb_id}/evaluations/batch` | 运行最多 25 题的批量评测 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations/runs` | 列出最近评测批次 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations/runs/{run_id}` | 读取批次指标和逐题结果 |

所有列表接口都接受 `limit`（1–200）与 `offset`，并在响应头返回 `X-Total-Count`、`X-Page-Limit`、`X-Page-Offset`、`X-Has-More`；每个响应还带 `X-Request-ID` 与 `X-Process-Time-Ms`，便于排查慢请求。

上传接口为 `multipart/form-data`，字段名是 `files`，可同时上传多个文件。上传结果中 `status` 为 `ready`、`duplicate`（内容与已有资料相同，沿用旧文档）或失败异常。问答 JSON 示例：

```json
{
  "kb_id": "知识库 UUID",
  "question": "这份资料介绍了哪些内容？",
  "conversation_id": null,
  "strategy": "hybrid",
  "k": 5,
  "history": true
}
```

`strategy` 可取 `similarity`、`mmr`、`hybrid`；`history` 为 `false` 时忽略该会话的历史上下文，适合做单轮回归测试。

批量评测接受 JSON 数组或 `cases` 对象，`expected_sources` 为可选的标准文件名列表：

```json
{
  "dataset_name": "退款政策回归集",
  "strategy": "mmr",
  "cases": [
    {
      "question": "退款期限是多久？",
      "expected_answer": "30 天",
      "expected_sources": ["refund-policy.pdf"]
    }
  ]
}
```

支持逐行 JSON（JSONL），每行是一个题目对象。文件上传在浏览器评测工作台完成，批次最多 25 题；模型调用失败会作为失败用例保留，不会丢失同批其他结果。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` 或 `openai` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b` | Ollama 对话模型 |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama 向量模型 |
| `OPENAI_API_KEY` | 空 | OpenAI / 兼容服务密钥 |
| `OPENAI_BASE_URL` | 空 | 可选的兼容 API 地址 |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | 对话模型名称 |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | 嵌入模型名称 |
| `DATA_DIR` | `./data` | SQLite、上传文件、Chroma 数据目录 |
| `CHUNK_SIZE` | `800` | 文本块字符数 |
| `CHUNK_OVERLAP` | `120` | 相邻文本块重叠字符数 |
| `RETRIEVAL_K` | `5` | 每次检索的片段数 |
| `RETRIEVAL_STRATEGY` | `similarity` | 默认 `similarity` / `mmr` / `hybrid`；聊天请求也可单独指定 |
| `RETRIEVAL_FETCH_K` | `20` | MMR 与混合检索重排前的候选片段数 |
| `RETRIEVAL_MIN_SCORE` | `0` | 最低相关度阈值，低于该分数的片段直接丢弃 |
| `MMR_LAMBDA` | `0.65` | MMR 相关性权重，范围 0–1；越高越偏相关性 |
| `HYBRID_VECTOR_WEIGHT` | `0.7` | 混合检索中向量分数的权重，其余部分为关键词重合度 |
| `HISTORY_TURNS` | `4` | 每次提问注入的历史轮数；设为 `0` 关闭多轮记忆 |

## 测试与评测说明

运行本地测试（共 29 个用例，使用模拟 Embeddings / 模型，不需要 Ollama 或 API Key）：

```bash
python -m unittest discover -s tests -v
```

覆盖范围：

- `test_store_migrations.py`：旧库原地升级到 `SCHEMA_VERSION`、WAL 与外键生效、统计聚合。
- `test_conversation_api.py`：多轮历史注入与 `history=false` 开关、SSE 事件序列与消息落库、会话重命名与分页头、请求 ID / 耗时头。
- `test_document_management_api.py`：内容去重、重命名与统计、文档详情流水线、重新索引、404 / 415 / 400 分支。
- `test_retrieval_strategies.py`：混合检索的排序差异、分数归一化与去重、最低分数过滤、`k` 越界收敛、提示词结构。
- `test_rag_api.py`、`test_batch_evaluation_api.py`：上传 → 检索 → 引用 → 评测的完整链路与批量指标。

静态检查：

```bash
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
npm ci
npm run check
```

单题的答案子串命中率是轻量基线；批量评测可选填标准来源文件名，并计算来源命中率、Precision@K、Recall@K、MRR 与平均模型延迟。来源指标在**去重后的文件级别**计算（同一文件命中多个片段只算一次）。答案子串命中不等于语义正确率；来源排序指标也不能替代回答忠实度、引用内容支持度或人工抽样复核。批量模型调用按题顺序同步运行，单批最多 25 题。

## 当前边界与后续增强

- 扫描版 PDF 需要 OCR；当前使用 PyPDF 文本抽取，遇到无文本 PDF 会提示没有可读取文本。
- 混合检索的关键词部分使用中英文 token 重合度（中文按字符二元组切分），尚未引入 BM25 或交叉编码器重排序。
- 流式回答基于 SSE，一次性完成；断线后不续传，也暂不支持中途取消正在进行的模型调用。
- Docker Compose 方案将 Ollama 与应用一同启动；切换 OpenAI provider 时可进一步增加不启动 Ollama 的 Compose profile。
- 面向生产部署时应补充用户认证、访问控制、异步任务队列、并发限流、备份恢复和多租户隔离。

## 版本记录

见 [CHANGELOG.md](./CHANGELOG.md)。
