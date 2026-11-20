import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt"}
RETRIEVAL_STRATEGIES = {"similarity", "mmr", "hybrid"}
MAX_RETRIEVAL_K = 20

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
RETRIEVAL_STRATEGY = os.getenv("RETRIEVAL_STRATEGY", "similarity").lower()
RETRIEVAL_FETCH_K = int(os.getenv("RETRIEVAL_FETCH_K", "20"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.65"))
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "0.7"))
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "4"))

SYSTEM_PROMPT = (
    "你是一个严谨的知识库问答助手。仅依据给定资料回答；资料不足时明确说明。"
    "使用中文回答，并在相关陈述后用 [1]、[2] 标注来源编号。不要编造引用。"
)
NO_CONTEXT_ANSWER = "当前知识库中没有检索到相关资料，请先上传文档或换个问题。"


def configured_embeddings() -> Embeddings:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        kwargs: dict[str, Any] = {
            "model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        }
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
        text = "\n".join(
            paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()
        )
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


def content_hash(data: bytes) -> str:
    """Content hash used to skip re-indexing an identical upload."""
    return hashlib.sha256(data).hexdigest()


def delete_document_vectors(kb_id: str, document_id: str) -> None:
    vector_store(kb_id).delete(where={"document_id": document_id})


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
    try:
        vector_store(kb_id).add_documents(chunks, ids=[str(uuid.uuid4()) for _ in chunks])
    except Exception:
        # A half-written collection would make later questions cite chunks that
        # no longer belong to any document row, so roll the partial write back.
        try:
            delete_document_vectors(kb_id, document_id)
        except Exception:
            pass
        raise
    return len(chunks)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot_product / (left_norm * right_norm)


_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def _token_set(text: str) -> set[str]:
    """Bilingual tokeniser: latin words plus CJK characters and bigrams.

    A real tokenizer (jieba etc.) is a heavy dependency for a demo project, and
    character bigrams are a good enough proxy for Chinese keyword overlap.
    """
    lowered = text.casefold()
    tokens = set(_WORD_PATTERN.findall(lowered))
    cjk = [char for char in lowered if "\u4e00" <= char <= "\u9fff"]
    tokens.update(cjk)
    tokens.update("".join(pair) for pair in zip(cjk, cjk[1:]))
    return tokens


def lexical_overlap(question: str, text: str) -> float:
    """Share of question tokens that also appear in ``text`` (0–1)."""
    query_tokens = _token_set(question)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _token_set(text)) / len(query_tokens)


def resolve_strategy(strategy: str | None) -> str:
    selected = (strategy or RETRIEVAL_STRATEGY).lower()
    if selected not in RETRIEVAL_STRATEGIES:
        raise ValueError(
            "Retrieval strategy must be one of: " + ", ".join(sorted(RETRIEVAL_STRATEGIES)) + "."
        )
    return selected


def resolve_k(k: int | None) -> int:
    requested = RETRIEVAL_K if k is None else int(k)
    return max(1, min(requested, MAX_RETRIEVAL_K))


def _similarity_from_distance(distance: float) -> float:
    return 1.0 / (1.0 + max(float(distance), 0.0))


def _dedupe(scored: list[tuple[Document, float]]) -> list[tuple[Document, float]]:
    """Drop repeated chunks, keeping the highest-scoring occurrence."""
    seen: set[tuple[str, Any, str]] = set()
    unique: list[tuple[Document, float]] = []
    for document, score in scored:
        key = (
            str(document.metadata.get("source") or ""),
            document.metadata.get("page"),
            hashlib.sha1(document.page_content.encode("utf-8")).hexdigest(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append((document, score))
    return unique


def retrieve_documents(
    kb_id: str,
    question: str,
    strategy: str | None = None,
    k: int | None = None,
    min_score: float | None = None,
) -> list[tuple[Document, float]]:
    """Return ``(document, relevance)`` pairs ranked by the chosen strategy.

    ``similarity`` ranks by vector distance, ``mmr`` trades relevance for
    diversity, and ``hybrid`` blends the vector score with literal keyword
    overlap so exact identifiers (order numbers, policy codes) still rank.
    """
    selected = resolve_strategy(strategy)
    limit = resolve_k(k)
    threshold = RETRIEVAL_MIN_SCORE if min_score is None else float(min_score)
    if not 0 <= MMR_LAMBDA <= 1:
        raise ValueError("MMR_LAMBDA must be between 0 and 1.")
    if not 0 <= HYBRID_VECTOR_WEIGHT <= 1:
        raise ValueError("HYBRID_VECTOR_WEIGHT must be between 0 and 1.")

    if selected == "mmr":
        embeddings = configured_embeddings()
        documents = vector_store(kb_id, embeddings).max_marginal_relevance_search(
            question,
            k=limit,
            fetch_k=max(limit, RETRIEVAL_FETCH_K),
            lambda_mult=MMR_LAMBDA,
        )
        if not documents:
            return []
        query_vector = embeddings.embed_query(question)
        document_vectors = embeddings.embed_documents(
            [document.page_content for document in documents]
        )
        scored = [
            (document, max(0.0, min(1.0, (_cosine_similarity(query_vector, vector) + 1) / 2)))
            for document, vector in zip(documents, document_vectors, strict=True)
        ]
    else:
        fetch_k = limit if selected == "similarity" else max(limit, RETRIEVAL_FETCH_K)
        results = vector_store(kb_id).similarity_search_with_score(question, k=fetch_k)
        scored = [(document, _similarity_from_distance(distance)) for document, distance in results]
        if selected == "hybrid":
            scored = [
                (
                    document,
                    HYBRID_VECTOR_WEIGHT * vector_score
                    + (1 - HYBRID_VECTOR_WEIGHT) * lexical_overlap(question, document.page_content),
                )
                for document, vector_score in scored
            ]
            scored.sort(key=lambda item: item[1], reverse=True)

    return [(document, score) for document, score in _dedupe(scored) if score >= threshold][:limit]


def build_messages(
    question: str,
    context_parts: list[str],
    history: Iterable[dict[str, Any]] | None = None,
) -> list[BaseMessage]:
    """Compose the prompt: retrieval context, recent turns, then the question."""
    messages: list[BaseMessage] = [
        SystemMessage(
            content=f"{SYSTEM_PROMPT}\n\n资料片段：\n" + "\n".join(context_parts),
        )
    ]
    for item in history or []:
        role = str(item.get("role") or "").lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role in {"assistant", "ai"}:
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=f"用户问题：{question}"))
    return messages


def prepare_answer(
    kb_id: str,
    question: str,
    strategy: str | None = None,
    history: Iterable[dict[str, Any]] | None = None,
    k: int | None = None,
) -> tuple[list[dict[str, Any]], list[BaseMessage]]:
    """Retrieve context and build the prompt shared by blocking and streaming paths."""
    selected = resolve_strategy(strategy)
    retrieved = retrieve_documents(kb_id, question, selected, k=k)
    sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    for index, (document, score) in enumerate(retrieved, start=1):
        sources.append(
            {
                "index": index,
                "filename": document.metadata.get("source", "Unknown"),
                "page": document.metadata.get("page"),
                "document_id": document.metadata.get("document_id"),
                "chunk": document.page_content[:1200],
                "score": round(score, 4),
                "retrieval_strategy": selected,
            }
        )
        context_parts.append(f"[{index}] {document.page_content}")
    if not context_parts:
        return [], []
    return sources, build_messages(question, context_parts, history)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _stream_model_text(model: BaseChatModel, messages: list[BaseMessage]) -> Iterator[str]:
    try:
        for chunk in model.stream(messages):
            text = _message_text(chunk)
            if text:
                yield text
    except NotImplementedError:
        yield _message_text(model.invoke(messages))


def answer_question(
    kb_id: str,
    question: str,
    strategy: str | None = None,
    history: Iterable[dict[str, Any]] | None = None,
    k: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    sources, messages = prepare_answer(kb_id, question, strategy, history=history, k=k)
    if not messages:
        return NO_CONTEXT_ANSWER, []
    model = configured_chat_model()
    return _message_text(model.invoke(messages)), sources


def iter_answer(
    kb_id: str,
    question: str,
    strategy: str | None = None,
    history: Iterable[dict[str, Any]] | None = None,
    k: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield ``sources`` → ``token``* → ``final`` events for SSE streaming."""
    started = time.perf_counter()
    selected = resolve_strategy(strategy)
    sources, messages = prepare_answer(kb_id, question, selected, history=history, k=k)
    yield {"type": "sources", "sources": sources, "retrieval_strategy": selected}
    if not messages:
        yield {"type": "token", "text": NO_CONTEXT_ANSWER}
        yield {
            "type": "final",
            "answer": NO_CONTEXT_ANSWER,
            "sources": [],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        return

    model = configured_chat_model()
    pieces: list[str] = []
    for text in _stream_model_text(model, messages):
        pieces.append(text)
        yield {"type": "token", "text": text}
    yield {
        "type": "final",
        "answer": "".join(pieces).strip(),
        "sources": sources,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
