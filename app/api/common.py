import sqlite3
from pathlib import Path

from fastapi import HTTPException

from app import store


def require_knowledge_base(db: sqlite3.Connection, kb_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Knowledge base not found.")
    return row


def upload_root() -> Path:
    return store.DATA_DIR / "uploads"
