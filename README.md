# 知问：多格式智能知识库问答平台

一个基于 **LangChain** 的全栈 RAG（检索增强生成）应用。用户可以创建多个知识库，上传 PDF、Word、Markdown、TXT 资料，自动解析和切分文本、生成向量索引，并获得带有原文片段与文档来源的问答结果。支持 Ollama 本地模型与 OpenAI 兼容模型。

> **GitHub 开源仓库：[kiki2-30/open-source-homework](https://github.com/kiki2-30/open-source-homework)**

## 功能一览

- **知识库管理**：创建、切换、删除知识库；各知识库文档、向量索引和聊天记录隔离。
- **多格式资料导入**：支持 `.pdf`、`.docx`、`.md`、`.markdown`、`.txt`，每份文件最大 25 MB。
- **文档处理流水线**：解析文档、递归文本切分、保存元数据、调用 Embeddings 并写入 Chroma；流水线记录可追溯。
- **RAG 问答**：LangChain 相似度检索 + 对话模型回答；提示模型只使用检索上下文作答，资料不足时说明。
- **可切换检索策略**：支持相似度 Top-K 与 MMR 多样性检索；可配置召回候选数量和相关性权重。
- **引用溯源**：展示引用编号、文件名、PDF 页码（若适用）、相关原文片段和相似度分数。
- **聊天记录**：按知识库持久化多轮问题、回答和引用，支持历史会话浏览、创建和删除。
- **评测工作台**：单题调试或导入 JSON / JSONL 批量评测集；统计答案命中率、来源命中率、Precision@K、Recall@K、MRR 和平均延迟，保留逐题结果和历史批次。
- **部署与质量保障**：Docker Compose 一键启动应用和 Ollama；GitHub Actions 在 Python 3.11 / 3.13 上自动运行测试。
- **模块化后端**：按知识库、会话、操作和评测职责拆分 FastAPI 路由；Python Ruff 和前端 Prettier 检查接入 CI。
- **轻量部署**：FastAPI 提供 REST API 与浏览器 UI；SQLite 存储管理数据，Chroma 持久化向量索引。

## 技术架构

```text
浏览器（原生 HTML / CSS / JavaScript）
        │ REST / multipart
        ▼
FastAPI ─────────────── SQLite（知识库、文档、聊天、流水线、评测）
   │
   ├── LangChain 文档解析与 RecursiveCharacterTextSplitter
   ├── LangChain Embeddings ── Ollama / OpenAI 兼容 API
   ├── Chroma 向量库（按知识库隔离、持久化）
   ├── LangChain Chat Model（检索上下文 + 来源编号提示词）
   └── 批量评测服务（回答质量 + 来源排序 + 延迟指标）
```

### 项目结构

```text
app/
├── api/
│   ├── common.py            # API 公共查询与资源路径
│   ├── conversations.py     # 问答及会话 API
│   ├── evaluations.py       # 批量评测 API 与指标聚合
│   ├── knowledge_bases.py   # 知识库、资料生命周期 API
│   └── operations.py        # 流水线与单题评测 API
├── static/
│   ├── app.js               # 浏览器交互、聊天、资料管理与评测工作台
│   ├── index.html           # 单页应用结构
│   └── style.css            # 响应式工作台样式
├── knowledge.py             # 文档解析、切分、向量检索与生成
├── main.py                  # 应用生命周期、路由注册与系统健康检查
└── store.py                 # SQLite schema、连接管理与流水线记录
tests/
├── test_batch_evaluation_api.py  # 批量评测、指标与失败部分结果
├── test_rag_api.py              # 上传、相似度/MMR 问答、引用与生命周期
└── test_store.py                # 数据库初始化、级联删除与持久化
examples/
├── documents/                   # 可直接上传的示例知识文档
└── evaluation/                  # 与文档配套的 JSONL 回归集
```

## 项目实现工作量

| 子系统 | 主要实现 | 可验证结果 |
|---|---|---|
| 文件摄取 | PDF 按页抽取、DOCX/文本解析、递归切分、元数据保留、Chroma 持久化和失败状态记录 | 上传真实示例文档后可检索原文和页码 |
| 检索生成 | 按知识库隔离 collection、Similarity/MMR 可切换、引用编号提示词和来源片段返回 | 同一问题可切换策略并查看来源 |
| 后端 API | 按知识库、会话、操作、评测拆分路由；提供文档和知识库级联清理 | OpenAPI 文档包含可运行的 REST 接口 |
| 数据持久化 | SQLite 管理知识库、文档、会话、消息、流水线和评测批次；外键级联与查询索引 | 重启后可读历史会话和评测批次 |
| 评测系统 | JSON / JSONL 数据集导入，逐题隔离失败，汇总答案命中、来源命中、Precision@K、Recall@K、MRR、延迟 | 附 5 题可运行示例回归集 |
| 全栈界面 | 响应式聊天、来源卡片、文档管理、流水线、策略选择与批次历史 | 浏览器端完成从导入到查看评测的操作 |
| 工程交付 | Docker Compose、环境变量配置、Python/JS 格式检查和多版本 GitHub Actions | 本地测试、CI 配置和容器启动说明 |

### 核心流程

1. 上传文件并按扩展名选择 PDF / DOCX / UTF-8 文本解析器。
2. 以 `CHUNK_SIZE`、`CHUNK_OVERLAP` 配置分块，保留文件名、知识库 ID、文档 ID、页码等元数据。
3. 通过配置的 Embeddings 生成向量并写入对应知识库的 Chroma collection。
4. 用户提问后选择相似度 Top-K 或 MMR 多样性检索，将片段编号注入模型上下文。
5. 回答、引用片段、引用文件与会话记录写入 SQLite，供前端查看和后续评测。

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
| `GET` | `/api/health` | 健康检查与模型 provider |
| `GET`, `POST` | `/api/knowledge-bases` | 列出 / 创建知识库 |
| `DELETE` | `/api/knowledge-bases/{kb_id}` | 删除知识库及其关联数据 |
| `GET`, `POST` | `/api/knowledge-bases/{kb_id}/documents` | 列出 / 上传资料 |
| `DELETE` | `/api/knowledge-bases/{kb_id}/documents/{document_id}` | 删除文档和对应向量 |
| `POST` | `/api/ask` | 提问并返回回答、引用、会话 ID |
| `GET` | `/api/knowledge-bases/{kb_id}/conversations` | 查看会话列表 |
| `GET` | `/api/conversations/{conversation_id}/messages` | 查看会话消息 |
| `DELETE` | `/api/conversations/{conversation_id}` | 删除会话 |
| `GET` | `/api/knowledge-bases/{kb_id}/pipeline` | 查看处理流水线 |
| `POST` | `/api/evaluate` | 执行一次问答评测 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations` | 查看评测历史与汇总 |
| `POST` | `/api/knowledge-bases/{kb_id}/evaluations/batch` | 运行最多 25 题的批量评测 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations/runs` | 列出最近评测批次 |
| `GET` | `/api/knowledge-bases/{kb_id}/evaluations/runs/{run_id}` | 读取批次指标和逐题结果 |

上传接口为 `multipart/form-data`，字段名是 `files`，可同时上传多个文件。问答 JSON 示例：

```json
{
  "kb_id": "知识库 UUID",
  "question": "这份资料介绍了哪些内容？",
  "conversation_id": null
}
```

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
| `RETRIEVAL_STRATEGY` | `similarity` | 默认 `similarity` 或 `mmr`；聊天请求也可单独指定 |
| `RETRIEVAL_FETCH_K` | `20` | MMR 重排前的候选片段数 |
| `MMR_LAMBDA` | `0.65` | MMR 相关性权重，范围 0–1；越高越偏相关性 |

## 测试与评测说明

运行本地测试（包括 SQLite 存储测试，以及使用模拟模型完成的上传、检索、引用、聊天记录和评测 API 流程）：

```bash
python -m unittest discover -s tests -v
```

静态检查：

```bash
.venv/bin/ruff check app tests
npm ci
npm run check
```

单题的答案子串命中率是轻量基线；批量评测可选填标准来源文件名，并计算来源命中率、Precision@K、Recall@K、MRR 与平均模型延迟。来源指标在**去重后的文件级别**计算（同一文件命中多个片段只算一次）。答案子串命中不等于语义正确率；来源排序指标也不能替代回答忠实度、引用内容支持度或人工抽样复核。批量模型调用按题顺序同步运行，单批最多 25 题。

## 当前边界与后续增强

- 扫描版 PDF 需要 OCR；当前使用 PyPDF 文本抽取，遇到无文本 PDF 会提示没有可读取文本。
- 当前检索提供相似度与 MMR 两种策略；混合检索、交叉编码器重排序和按批次版本对比可继续扩展。
- Docker Compose 方案将 Ollama 与应用一同启动；切换 OpenAI provider 时可进一步增加不启动 Ollama 的 Compose profile。
- 面向生产部署时应补充用户认证、访问控制、异步任务队列、并发限流、备份恢复和多租户隔离。
