import os
import uuid
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import store


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt"}
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))


def configured_embeddings() -> Embeddings:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        kwargs: dict[str, Any] = {"model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")}
        if os.getenv("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
        return OpenAIEmbeddings(**kwargs)
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    raise ValueError("LLM_PROVIDER must be 'ollama' or 'openai'.")


def configured_chat_model() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            "temperature": 0.1,
        }
        if os.getenv("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
        return ChatOpenAI(**kwargs)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.1,
        )
    raise ValueError("LLM_PROVIDER must be 'ollama' or 'openai'.")


def vector_store(kb_id: str, embeddings: Embeddings | None = None):
    from langchain_chroma import Chroma

    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
    (data_dir / "chroma").mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=f"kb_{kb_id.replace('-', '_')}",
        persist_directory=str(data_dir / "chroma"),
        embedding_function=embeddings or configured_embeddings(),
    )


def load_documents(path: Path, kb_id: str, document_id: str, filename: str) -> list[Document]:
    extension = path.suffix.lower()
    if extension == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        documents = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": filename,
                            "document_id": document_id,
                            "knowledge_base_id": kb_id,
                            "page": page_number,
                        },
                    )
                )
        return documents
    if extension == ".docx":
        from docx import Document as WordDocument

        document = WordDocument(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    else:
        text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return []
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "document_id": document_id, "knowledge_base_id": kb_id},
        )
    ]


def ingest_file(kb_id: str, document_id: str, filename: str, path: Path) -> int:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {path.suffix or '(none)'}")
    documents = load_documents(path, kb_id, document_id, filename)
    if not documents:
        raise ValueError("No readable text was found in the document.")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    vector_store(kb_id).add_documents(chunks, ids=[str(uuid.uuid4()) for _ in chunks])
    return len(chunks)


def answer_question(kb_id: str, question: str) -> tuple[str, list[dict[str, Any]]]:
    retrieved = vector_store(kb_id).similarity_search_with_score(question, k=RETRIEVAL_K)
    sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    for index, (document, distance) in enumerate(retrieved, start=1):
        score = max(0.0, min(1.0, 1.0 - float(distance) / 2.0))
        source = {
            "index": index,
            "filename": document.metadata.get("source", "Unknown"),
            "page": document.metadata.get("page"),
            "chunk": document.page_content[:1200],
            "score": round(score, 4),
        }
        sources.append(source)
        context_parts.append(f"[{index}] {document.page_content}")

    if not context_parts:
        return "当前知识库中没有检索到相关资料，请先上传文档或换个问题。", []

    model = configured_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "你是一个严谨的知识库问答助手。仅依据给定资料回答；资料不足时明确说明。"
                    "使用中文回答，并在相关陈述后用 [1]、[2] 标注来源编号。不要编造引用。"
                )
            ),
            HumanMessage(
                content=f"资料片段：\n{chr(10).join(context_parts)}\n\n用户问题：{question}"
            ),
        ]
    )
    answer = response.content
    if not isinstance(answer, str):
        answer = str(answer)
    return answer, sources
