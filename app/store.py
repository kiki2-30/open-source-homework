import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "knowledge.db"

# Bumped whenever SCHEMA gains a column or index that existing databases must
# pick up through _migrate().
SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection with sane concurrency settings.

    FastAPI serves requests from a thread pool, so several connections may
    write at once. WAL plus a busy timeout keeps readers from blocking writers
    and avoids "database is locked" errors under light concurrent load.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    try:
        with db:
            yield db
    finally:
        db.close()


SCHEMA = """
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                filename TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, error TEXT, content_hash TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                document_id TEXT, stage TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                question TEXT NOT NULL, expected TEXT NOT NULL, answer TEXT NOT NULL,
                hit INTEGER NOT NULL, citation_count INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                dataset_name TEXT NOT NULL, status TEXT NOT NULL, metrics TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_cases (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
                position INTEGER NOT NULL, question TEXT NOT NULL, expected_answer TEXT NOT NULL,
                expected_sources TEXT NOT NULL, answer TEXT NOT NULL, retrieved_sources TEXT NOT NULL,
                answer_hit INTEGER, source_hit INTEGER, source_precision REAL, source_recall REAL,
                reciprocal_rank REAL,
                latency_ms REAL, status TEXT NOT NULL, error TEXT,
                created_at TEXT NOT NULL, UNIQUE(run_id, position)
            );
            """


# Indexes are created after _migrate(): a legacy database can lack the columns
# they reference until the migration has run.
INDEXES = """
            CREATE INDEX IF NOT EXISTS idx_evaluation_runs_kb_created
                ON evaluation_runs(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_evaluation_cases_run_position
                ON evaluation_cases(run_id, position);
            CREATE INDEX IF NOT EXISTS idx_documents_kb_created
                ON documents(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_documents_kb_hash
                ON documents(kb_id, content_hash);
            CREATE INDEX IF NOT EXISTS idx_conversations_kb_updated
                ON conversations(kb_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                ON messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_kb_created
                ON pipeline_runs(kb_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_evaluations_kb_created
                ON evaluations(kb_id, created_at DESC);
            """


def _migrate(db: sqlite3.Connection) -> None:
    """Bring an existing database up to SCHEMA_VERSION.

    ``CREATE TABLE IF NOT EXISTS`` never adds columns to a table that already
    exists, so every column added after the first release needs an explicit
    step here. Steps must stay idempotent and ordered.
    """
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 2:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
        if "content_hash" not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
        kb_columns = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_bases)")}
        if "updated_at" not in kb_columns:
            db.execute("ALTER TABLE knowledge_bases ADD COLUMN updated_at TEXT")
        db.execute("UPDATE knowledge_bases SET updated_at = created_at WHERE updated_at IS NULL")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def initialize() -> None:
    with connect() as db:
        db.executescript(SCHEMA)
        _migrate(db)
        db.executescript(INDEXES)


def record_pipeline(
    kb_id: str, stage: str, status: str, details: str, document_id: str | None = None
) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), kb_id, document_id, stage, status, details, now()),
        )


def rows_as_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def fetch_all(db: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return rows_as_dicts(db.execute(sql, tuple(params)).fetchall())


def fetch_one(
    db: sqlite3.Connection, sql: str, params: Iterable[Any] = ()
) -> dict[str, Any] | None:
    row = db.execute(sql, tuple(params)).fetchone()
    return dict(row) if row is not None else None


def scalar(db: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    row = db.execute(sql, tuple(params)).fetchone()
    return row[0] if row is not None else None


def parse_sources(raw: str) -> list[dict[str, Any]]:
    return json.loads(raw or "[]")


def knowledge_base_stats(db: sqlite3.Connection, kb_id: str) -> dict[str, Any]:
    """Aggregate per-knowledge-base counters for the workspace overview."""
    row = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM documents WHERE kb_id = :kb) AS documents,
            (SELECT COALESCE(SUM(chunk_count), 0) FROM documents
                WHERE kb_id = :kb AND status = 'ready') AS chunks,
            (SELECT COUNT(*) FROM documents
                WHERE kb_id = :kb AND status = 'failed') AS failed_documents,
            (SELECT COUNT(*) FROM conversations WHERE kb_id = :kb) AS conversations,
            (SELECT COUNT(*) FROM messages
                WHERE conversation_id IN (SELECT id FROM conversations WHERE kb_id = :kb))
                AS messages,
            (SELECT COUNT(*) FROM evaluation_runs WHERE kb_id = :kb) AS evaluation_runs,
            (SELECT COUNT(*) FROM pipeline_runs
                WHERE kb_id = :kb AND status = 'failed') AS failed_pipeline_runs,
            (SELECT MAX(created_at) FROM documents WHERE kb_id = :kb) AS last_document_at,
            (SELECT MAX(updated_at) FROM conversations WHERE kb_id = :kb) AS last_conversation_at
        """,
        {"kb": kb_id},
    ).fetchone()
    stats = dict(row)
    stats["knowledge_base_id"] = kb_id
    return stats
