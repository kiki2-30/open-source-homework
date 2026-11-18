# 知问：多格式智能知识库问答平台

一个基于 **LangChain** 的全栈 RAG（检索增强生成）应用。用户可以创建多个知识库，上传 PDF、Word、Markdown、TXT 资料，自动解析和切分文本、生成向量索引，并获得带有原文片段与文档来源的问答结果。支持 Ollama 本地模型与 OpenAI 兼容模型。

> **GitHub 开源仓库：[kiki2-30/open-source-homework](https://github.com/kiki2-30/open-source-homework)**

## 功能一览

- **知识库管理**：创建、切换、删除知识库；各知识库文档、向量索引和聊天记录隔离。
- **多格式资料导入**：支持 `.pdf`、`.docx`、`.md`、`.markdown`、`.txt`，每份文件最大 25 MB。
- **文档处理流水线**：解析文档、递归文本切分、保存元数据、调用 Embeddings 并写入 Chroma；流水线记录可追溯。
- **RAG 问答**：LangChain 相似度检索 + 对话模型回答；提示模型只使用检索上下文作答，资料不足时说明。
- **引用溯源**：展示引用编号、文件名、PDF 页码（若适用）、相关原文片段和相似度分数。
- **聊天记录**：按知识库持久化多轮问题、回答和引用，支持历史会话浏览、创建和删除。
- **评测工作台**：输入测试问题与期望答案，记录答案子串命中率、引用数量与历史评测数据。
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
   └── LangChain Chat Model（检索上下文 + 来源编号提示词）
```

### 核心流程

1. 上传文件并按扩展名选择 PDF / DOCX / UTF-8 文本解析器。
2. 以 `CHUNK_SIZE`、`CHUNK_OVERLAP` 配置分块，保留文件名、知识库 ID、文档 ID、页码等元数据。
3. 通过配置的 Embeddings 生成向量并写入对应知识库的 Chroma collection。
4. 用户提问后执行相似度检索，默认取前 5 个片段，将片段编号注入模型上下文。
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
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000> 使用页面，或打开 <http://127.0.0.1:8000/docs> 调试 API。

首次上传时，应用会在 `data/` 下创建 SQLite 数据库、上传文件和 Chroma 索引。上传需要 Embeddings 服务可连接；问答还需要聊天模型服务可用。

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

上传接口为 `multipart/form-data`，字段名是 `files`，可同时上传多个文件。问答 JSON 示例：

```json
{
  "kb_id": "知识库 UUID",
  "question": "这份资料介绍了哪些内容？",
  "conversation_id": null
}
```

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

## 测试与评测说明

运行本地测试（包括 SQLite 存储测试，以及使用模拟模型完成的上传、检索、引用、聊天记录和评测 API 流程）：

```bash
python -m unittest discover -s tests -v
```

页面的答案子串命中率是轻量基线：检查人工期望答案是否作为连续子串出现在生成文本中。它不等于语义正确率，也不评估检索召回率、忠实度或模型偏差。正式评测建议准备带有标准答案和标准来源的测试集，并补充 Recall@K、MRR、引用精确率/召回率、忠实度及人工抽样复核。

## 当前边界与后续增强

- 扫描版 PDF 需要 OCR；当前使用 PyPDF 文本抽取，遇到无文本 PDF 会提示没有可读取文本。
- 当前知识库检索按相似度 Top-K；可按数据集需求增加 MMR、混合检索和重排序器。
- 评测界面当前按问题逐条录入；可扩展为 CSV/JSONL 批量评测和版本对比。
- 面向生产部署时应补充用户认证、访问控制、异步任务队列、并发限流、备份恢复和多租户隔离。
