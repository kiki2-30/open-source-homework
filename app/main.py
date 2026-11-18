import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import store
from app.api.conversations import router as conversations_router
from app.api.evaluations import router as evaluations_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.operations import router as operations_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    store.initialize()
    yield


app = FastAPI(title="知问 · 智能知识库", version="1.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(knowledge_bases_router)
app.include_router(conversations_router)
app.include_router(operations_router)
app.include_router(evaluations_router)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "provider": os.getenv("LLM_PROVIDER", "ollama")}
