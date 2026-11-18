import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import require_knowledge_base, upload_root
from app.knowledge import SUPPORTED_EXTENSIONS, ingest_file, vector_store

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])
MAX_FILE_BYTES = 25 * 1024 * 1024


class KnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


@router.get("")
def list_knowledge_bases() -> list[dict[str, Any]]:
    with store.connect() as db:
        return store.rows_as_dicts(
            db.execute(
                """SELECT kb.*, COUNT(d.id) AS document_count
                   FROM knowledge_bases kb LEFT JOIN documents d
                   ON d.kb_id = kb.id AND d.status = 'ready'
                   GROUP BY kb.id ORDER BY kb.created_at DESC"""
            ).fetchall()
        )


@router.post("", status_code=201)
def create_knowledge_base(payload: KnowledgeBaseInput) -> dict[str, str]:
    kb_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute(
            "INSERT INTO knowledge_bases (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (kb_id, payload.name, payload.description, store.now()),
        )
    return {"id": kb_id, "name": payload.name}


@router.delete("/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str) -> None:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
    try:
        vector_store(kb_id).delete_collection()
    except Exception as exc:
        raise HTTPException(500, f"Failed to remove knowledge-base vectors: {exc}") from exc
    with store.connect() as db:
        db.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
    upload_directory = upload_root() / kb_id
    if upload_directory.is_dir():
        shutil.rmtree(upload_directory)


@router.get("/{kb_id}/documents")
def list_documents(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        return store.rows_as_dicts(
            db.execute(
                "SELECT * FROM documents WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,)
            ).fetchall()
        )


@router.post("/{kb_id}/documents", status_code=201)
async def upload_documents(kb_id: str, files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
    results = []
    for upload in files:
        filename = Path(upload.filename or "document").name
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise HTTPException(415, f"Unsupported file type: {filename}")
        contents = await upload.read(MAX_FILE_BYTES + 1)
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename} exceeds the 25 MB upload limit.")
        document_id = str(uuid.uuid4())
        destination = upload_root() / kb_id / f"{document_id}{extension}"
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
                kb_id,
                "parse_and_index",
                "completed",
                f"{filename}: {chunk_count} chunks indexed",
                document_id,
            )
            results.append(
                {
                    "id": document_id,
                    "filename": filename,
                    "chunk_count": chunk_count,
                    "status": "ready",
                }
            )
        except Exception as exc:
            with store.connect() as db:
                db.execute(
                    "UPDATE documents SET status = 'failed', error = ? WHERE id = ?",
                    (str(exc), document_id),
                )
            store.record_pipeline(kb_id, "parse_and_index", "failed", str(exc), document_id)
            raise HTTPException(500, f"Failed to process {filename}: {exc}") from exc
    return results


@router.delete("/{kb_id}/documents/{document_id}", status_code=204)
def delete_document(kb_id: str, document_id: str) -> None:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        document = db.execute(
            "SELECT * FROM documents WHERE id = ? AND kb_id = ?", (document_id, kb_id)
        ).fetchone()
        if document is None:
            raise HTTPException(404, "Document not found.")
    try:
        vector_store(kb_id).delete(where={"document_id": document_id})
    except Exception as exc:
        raise HTTPException(500, f"Failed to remove document vectors: {exc}") from exc
    for path in (upload_root() / kb_id).glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
    with store.connect() as db:
        db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
