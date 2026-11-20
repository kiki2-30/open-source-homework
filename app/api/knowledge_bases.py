import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import (
    Limit,
    Offset,
    pagination_headers,
    require_knowledge_base,
    upload_root,
)
from app.knowledge import (
    SUPPORTED_EXTENSIONS,
    content_hash,
    delete_document_vectors,
    ingest_file,
    vector_store,
)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])
MAX_FILE_BYTES = 25 * 1024 * 1024


class KnowledgeBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class KnowledgeBaseUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


@router.get("")
def list_knowledge_bases(
    response: Response, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    with store.connect() as db:
        total = store.scalar(db, "SELECT COUNT(*) FROM knowledge_bases") or 0
        items = store.fetch_all(
            db,
            """SELECT kb.*, COUNT(d.id) AS document_count,
                      COALESCE(SUM(d.chunk_count), 0) AS chunk_count
               FROM knowledge_bases kb LEFT JOIN documents d
               ON d.kb_id = kb.id AND d.status = 'ready'
               GROUP BY kb.id ORDER BY kb.created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )
    response.headers.update(pagination_headers(total, limit, offset))
    return items


@router.post("", status_code=201)
def create_knowledge_base(payload: KnowledgeBaseInput) -> dict[str, str]:
    kb_id = str(uuid.uuid4())
    timestamp = store.now()
    with store.connect() as db:
        db.execute(
            """INSERT INTO knowledge_bases (id, name, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (kb_id, payload.name, payload.description, timestamp, timestamp),
        )
    return {"id": kb_id, "name": payload.name}


@router.get("/{kb_id}")
def get_knowledge_base(kb_id: str) -> dict[str, Any]:
    with store.connect() as db:
        knowledge_base = dict(require_knowledge_base(db, kb_id))
        knowledge_base["stats"] = store.knowledge_base_stats(db, kb_id)
    return knowledge_base


@router.patch("/{kb_id}")
def update_knowledge_base(kb_id: str, payload: KnowledgeBaseUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Provide at least one of: name, description.")
    assignments = ", ".join(f"{column} = ?" for column in updates)
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        db.execute(
            f"UPDATE knowledge_bases SET {assignments}, updated_at = ? WHERE id = ?",
            (*updates.values(), store.now(), kb_id),
        )
        knowledge_base = dict(require_knowledge_base(db, kb_id))
    return knowledge_base


@router.get("/{kb_id}/stats")
def knowledge_base_stats(kb_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        return store.knowledge_base_stats(db, kb_id)


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
def list_documents(
    kb_id: str,
    response: Response,
    status: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    clauses = ["kb_id = ?"]
    params: list[Any] = [kb_id]
    if status:
        if status not in {"processing", "ready", "failed"}:
            raise HTTPException(400, "status must be processing, ready or failed.")
        clauses.append("status = ?")
        params.append(status)
    where = " AND ".join(clauses)
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        total = store.scalar(db, f"SELECT COUNT(*) FROM documents WHERE {where}", params) or 0
        items = store.fetch_all(
            db,
            f"SELECT * FROM documents WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
    response.headers.update(pagination_headers(total, limit, offset))
    return items


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
        digest = content_hash(contents)

        with store.connect() as db:
            duplicate = db.execute(
                """SELECT id, filename, chunk_count FROM documents
                   WHERE kb_id = ? AND content_hash = ? AND status = 'ready' LIMIT 1""",
                (kb_id, digest),
            ).fetchone()
        if duplicate is not None:
            store.record_pipeline(
                kb_id, "dedupe", "skipped", f"{filename} duplicates {duplicate['filename']}"
            )
            results.append(
                {
                    "id": duplicate["id"],
                    "filename": filename,
                    "chunk_count": duplicate["chunk_count"],
                    "status": "duplicate",
                    "duplicate_of": duplicate["filename"],
                }
            )
            continue

        document_id = str(uuid.uuid4())
        destination = upload_root() / kb_id / f"{document_id}{extension}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
        with store.connect() as db:
            db.execute(
                """INSERT INTO documents
                   (id, kb_id, filename, chunk_count, status, error, content_hash, created_at)
                   VALUES (?, ?, ?, 0, 'processing', NULL, ?, ?)""",
                (document_id, kb_id, filename, digest, store.now()),
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


def _document_or_404(db: Any, kb_id: str, document_id: str) -> Any:
    document = db.execute(
        "SELECT * FROM documents WHERE id = ? AND kb_id = ?", (document_id, kb_id)
    ).fetchone()
    if document is None:
        raise HTTPException(404, "Document not found.")
    return document


@router.get("/{kb_id}/documents/{document_id}")
def get_document(kb_id: str, document_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        document = dict(_document_or_404(db, kb_id, document_id))
        document["pipeline"] = store.fetch_all(
            db,
            "SELECT * FROM pipeline_runs WHERE document_id = ? ORDER BY created_at, rowid",
            (document_id,),
        )
    return document


@router.post("/{kb_id}/documents/{document_id}/reindex")
def reindex_document(kb_id: str, document_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        document = dict(_document_or_404(db, kb_id, document_id))
    stored_files = sorted((upload_root() / kb_id).glob(f"{document_id}.*"))
    if not stored_files:
        raise HTTPException(409, "The original upload is gone; upload the file again.")
    path = stored_files[0]

    with store.connect() as db:
        db.execute(
            "UPDATE documents SET status = 'processing', error = NULL WHERE id = ?", (document_id,)
        )
    try:
        delete_document_vectors(kb_id, document_id)
        chunk_count = ingest_file(kb_id, document_id, document["filename"], path)
    except Exception as exc:
        with store.connect() as db:
            db.execute(
                "UPDATE documents SET status = 'failed', error = ? WHERE id = ?",
                (str(exc), document_id),
            )
        store.record_pipeline(kb_id, "reindex", "failed", str(exc), document_id)
        raise HTTPException(500, f"Failed to reindex {document['filename']}: {exc}") from exc

    with store.connect() as db:
        db.execute(
            "UPDATE documents SET chunk_count = ?, status = 'ready' WHERE id = ?",
            (chunk_count, document_id),
        )
    store.record_pipeline(
        kb_id,
        "reindex",
        "completed",
        f"{document['filename']}: {chunk_count} chunks re-indexed",
        document_id,
    )
    return {
        "id": document_id,
        "filename": document["filename"],
        "chunk_count": chunk_count,
        "status": "ready",
    }


@router.delete("/{kb_id}/documents/{document_id}", status_code=204)
def delete_document(kb_id: str, document_id: str) -> None:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        _document_or_404(db, kb_id, document_id)
    try:
        delete_document_vectors(kb_id, document_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to remove document vectors: {exc}") from exc
    for path in (upload_root() / kb_id).glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
    with store.connect() as db:
        db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
