import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException, Query

from app import store

MAX_PAGE_SIZE = 200

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum rows to return.")]
Offset = Annotated[int, Query(ge=0, description="Rows to skip before returning results.")]


def require_knowledge_base(db: sqlite3.Connection, kb_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Knowledge base not found.")
    return row


def upload_root() -> Path:
    return store.DATA_DIR / "uploads"


def pagination_headers(total: int, limit: int, offset: int) -> dict[str, str]:
    """Expose pagination metadata without changing list response bodies."""
    return {
        "X-Total-Count": str(total),
        "X-Page-Limit": str(limit),
        "X-Page-Offset": str(offset),
        "X-Has-More": "true" if offset + limit < total else "false",
    }
