import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import store
from app.api.conversations import router as conversations_router
from app.api.evaluations import router as evaluations_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.operations import router as operations_router

APP_VERSION = "1.2.0"

TAGS_METADATA = [
    {"name": "system", "description": "健康检查与运行时信息。"},
    {"name": "knowledge-bases", "description": "知识库、资料上传、去重与重新索引。"},
    {"name": "conversations", "description": "多轮问答、SSE 流式回答与会话历史。"},
    {"name": "operations", "description": "处理流水线与单题评测。"},
    {"name": "evaluations", "description": "批量评测、指标聚合与历史批次。"},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    store.initialize()
    yield


app = FastAPI(
    title="知问 · 智能知识库",
    version=APP_VERSION,
    description="基于 LangChain 与 Chroma 的多格式知识库 RAG 问答与评测平台。",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(knowledge_bases_router)
app.include_router(conversations_router)
app.include_router(operations_router)
app.include_router(evaluations_router)


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Any:
    """Attach a correlation id and server-side duration to every response."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": request_id},
    )


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health", tags=["system"])
def health() -> dict[str, Any]:
    """Liveness probe that also reports storage health and workspace totals."""
    payload: dict[str, Any] = {
        "status": "ok",
        "version": APP_VERSION,
        "provider": os.getenv("LLM_PROVIDER", "ollama").lower(),
        "retrieval_strategy": os.getenv("RETRIEVAL_STRATEGY", "similarity").lower(),
        "database": "ok",
        "knowledge_bases": 0,
        "documents": 0,
    }
    try:
        with store.connect() as db:
            db.execute("SELECT 1").fetchone()
            payload["knowledge_bases"] = store.scalar(db, "SELECT COUNT(*) FROM knowledge_bases")
            payload["documents"] = store.scalar(db, "SELECT COUNT(*) FROM documents")
    except Exception:
        payload["status"] = "degraded"
        payload["database"] = "unavailable"
    return payload
