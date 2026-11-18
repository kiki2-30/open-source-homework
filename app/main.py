import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.knowledge import SUPPORTED_EXTENSIONS, answer_question, ingest_file, vector_store


MAX_FILE_BYTES = 25 * 1024 * 1024
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="知问 · 智能知识库", version="1.0.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class KnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class AskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kb_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class EvaluationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kb_id: str
    question: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=4000)


def require_kb(db: sqlite3.Connection, kb_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Knowledge base not found.")
    return row


@app.on_event("startup")
def startup() -> None:
    store.initialize()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "provider": os.getenv("LLM_PROVIDER", "ollama")}


@app.get("/api/knowledge-bases")
def list_knowledge_bases() -> list[dict[str, Any]]:
    with store.connect() as db:
        return store.rows_as_dicts(
            db.execute(
                """SELECT kb.*, COUNT(d.id) AS document_count
                   FROM knowledge_bases kb LEFT JOIN documents d ON d.kb_id = kb.id AND d.status = 'ready'
                   GROUP BY kb.id ORDER BY kb.created_at DESC"""
            ).fetchall()
        )


@app.post("/api/knowledge-bases", status_code=201)
def create_knowledge_base(payload: KnowledgeBaseInput) -> dict[str, str]:
    kb_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute(
            "INSERT INTO knowledge_bases (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (kb_id, payload.name.strip(), payload.description.strip(), store.now()),
        )
    return {"id": kb_id, "name": payload.name.strip()}


@app.delete("/api/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str) -> None:
    with store.connect() as db:
        require_kb(db, kb_id)
    try:
        vector_store(kb_id).delete_collection()
    except Exception as exc:
        raise HTTPException(500, f"Failed to remove knowledge-base vectors: {exc}") from exc
    with store.connect() as db:
        db.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
    upload_directory = UPLOAD_DIR / kb_id
    if upload_directory.is_dir():
        shutil.rmtree(upload_directory)


@app.get("/api/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_kb(db, kb_id)
        return store.rows_as_dicts(
            db.execute("SELECT * FROM documents WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,)).fetchall()
        )


@app.post("/api/knowledge-bases/{kb_id}/documents", status_code=201)
async def upload_documents(kb_id: str, files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_kb(db, kb_id)
    results = []
    for upload in files:
        filename = Path(upload.filename or "document").name
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(415, f"Unsupported file type: {filename}")
        contents = await upload.read(MAX_FILE_BYTES + 1)
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename} exceeds the 25 MB upload limit.")
        document_id = str(uuid.uuid4())
        destination = UPLOAD_DIR / kb_id / f"{document_id}{Path(filename).suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
        with store.connect() as db:
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                (document_id, kb_id, filename, 0, "processing", None, store.now()),
            )
        store.record_pipeline(kb_id, "upload", "completed", f"Received {filename}", document_id)
        try:
            chunk_count = ingest_file(kb_id, document_id, filename, destination)
            with store.connect() as db:
                db.execute(
                    "UPDATE documents SET chunk_count = ?, status = 'ready' WHERE id = ?",
                    (chunk_count, document_id),
                )
            store.record_pipeline(
                kb_id, "parse_and_index", "completed", f"{filename}: {chunk_count} chunks indexed", document_id
            )
            results.append({"id": document_id, "filename": filename, "chunk_count": chunk_count, "status": "ready"})
        except Exception as exc:
            with store.connect() as db:
                db.execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (str(exc), document_id))
            store.record_pipeline(kb_id, "parse_and_index", "failed", str(exc), document_id)
            raise HTTPException(500, f"Failed to process {filename}: {exc}") from exc
    return results


@app.delete("/api/knowledge-bases/{kb_id}/documents/{document_id}", status_code=204)
def delete_document(kb_id: str, document_id: str) -> None:
    with store.connect() as db:
        require_kb(db, kb_id)
        document = db.execute(
            "SELECT * FROM documents WHERE id = ? AND kb_id = ?", (document_id, kb_id)
        ).fetchone()
        if document is None:
            raise HTTPException(404, "Document not found.")
    try:
        vector_store(kb_id).delete(where={"document_id": document_id})
    except Exception as exc:
        raise HTTPException(500, f"Failed to remove document vectors: {exc}") from exc
    for path in (UPLOAD_DIR / kb_id).glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
    with store.connect() as db:
        db.execute("DELETE FROM documents WHERE id = ?", (document_id,))


@app.post("/api/ask")
def ask(payload: AskInput) -> dict[str, Any]:
    with store.connect() as db:
        require_kb(db, payload.kb_id)
        conversation_id = payload.conversation_id
        if conversation_id:
            conversation = db.execute(
                "SELECT id FROM conversations WHERE id = ? AND kb_id = ?",
                (conversation_id, payload.kb_id),
            ).fetchone()
            if conversation is None:
                raise HTTPException(404, "Conversation not found in this knowledge base.")
        else:
            conversation_id = str(uuid.uuid4())
            title = re.sub(r"\s+", " ", payload.question).strip()[:48]
            db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                (conversation_id, payload.kb_id, title, store.now(), store.now()),
            )
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'user', ?, '[]', ?)",
            (str(uuid.uuid4()), conversation_id, payload.question, store.now()),
        )
    try:
        answer, sources = answer_question(payload.kb_id, payload.question)
        store.record_pipeline(
            payload.kb_id, "retrieve_and_generate", "completed",
            f"{len(sources)} sources used for question",
        )
    except Exception as exc:
        store.record_pipeline(payload.kb_id, "retrieve_and_generate", "failed", str(exc))
        raise HTTPException(502, f"Question answering failed: {exc}") from exc
    with store.connect() as db:
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, answer, json.dumps(sources, ensure_ascii=False), store.now()),
        )
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (store.now(), conversation_id))
    return {"conversation_id": conversation_id, "answer": answer, "sources": sources}


@app.get("/api/knowledge-bases/{kb_id}/conversations")
def conversations(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_kb(db, kb_id)
        return store.rows_as_dicts(
            db.execute(
                "SELECT * FROM conversations WHERE kb_id = ? ORDER BY updated_at DESC",
                (kb_id,),
            ).fetchall()
        )


@app.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        conversation = db.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
        messages = store.rows_as_dicts(
            db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        )
    for message in messages:
        message["sources"] = store.parse_sources(message["sources"])
    return messages


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    with store.connect() as db:
        cursor = db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Conversation not found.")


@app.get("/api/knowledge-bases/{kb_id}/pipeline")
def pipeline_runs(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_kb(db, kb_id)
        return store.rows_as_dicts(
            db.execute(
                "SELECT * FROM pipeline_runs WHERE kb_id = ? ORDER BY created_at DESC LIMIT 100",
                (kb_id,),
            ).fetchall()
        )


@app.post("/api/evaluate", status_code=201)
def evaluate(payload: EvaluationInput) -> dict[str, Any]:
    with store.connect() as db:
        require_kb(db, payload.kb_id)
    try:
        answer, sources = answer_question(payload.kb_id, payload.question)
    except Exception as exc:
        raise HTTPException(502, f"Evaluation run failed: {exc}") from exc
    hit = int(payload.expected.casefold() in answer.casefold())
    with store.connect() as db:
        db.execute(
            "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), payload.kb_id, payload.question, payload.expected,
                answer, hit, len(sources), store.now(),
            ),
        )
        total, hits = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(hit), 0) FROM evaluations WHERE kb_id = ?",
            (payload.kb_id,),
        ).fetchone()
    return {
        "answer": answer,
        "sources": sources,
        "exact_substring_hit": bool(hit),
        "citation_count": len(sources),
        "aggregate": {"cases": total, "substring_hit_rate": round(hits / total, 4) if total else 0},
    }


@app.get("/api/knowledge-bases/{kb_id}/evaluations")
def evaluation_history(kb_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_kb(db, kb_id)
        rows = store.rows_as_dicts(
            db.execute(
                "SELECT * FROM evaluations WHERE kb_id = ? ORDER BY created_at DESC LIMIT 100",
                (kb_id,),
            ).fetchall()
        )
        total, hits = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(hit), 0) FROM evaluations WHERE kb_id = ?",
            (kb_id,),
        ).fetchone()
    return {
        "items": rows,
        "aggregate": {"cases": total, "substring_hit_rate": round(hits / total, 4) if total else 0},
    }
