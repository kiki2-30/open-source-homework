import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import store

LEGACY_SCHEMA = """
CREATE TABLE knowledge_bases (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE pipeline_runs (
    id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id TEXT, stage TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE evaluations (
    id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    question TEXT NOT NULL, expected TEXT NOT NULL, answer TEXT NOT NULL,
    hit INTEGER NOT NULL, citation_count INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE evaluation_runs (
    id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    dataset_name TEXT NOT NULL, status TEXT NOT NULL, metrics TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE evaluation_cases (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, question TEXT NOT NULL, expected_answer TEXT NOT NULL,
    expected_sources TEXT NOT NULL, answer TEXT NOT NULL, retrieved_sources TEXT NOT NULL,
    answer_hit INTEGER, source_hit INTEGER, source_precision REAL, source_recall REAL,
    reciprocal_rank REAL, latency_ms REAL, status TEXT NOT NULL, error TEXT,
    created_at TEXT NOT NULL, UNIQUE(run_id, position)
);
PRAGMA user_version = 1;
"""


class StoreMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_data_dir = store.DATA_DIR
        self.previous_db_path = store.DB_PATH
        store.DATA_DIR = Path(self.temp_dir.name)
        store.DB_PATH = store.DATA_DIR / "legacy.sqlite3"

    def tearDown(self) -> None:
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_initialize_upgrades_legacy_database_in_place(self) -> None:
        legacy = sqlite3.connect(store.DB_PATH)
        legacy.executescript(LEGACY_SCHEMA)
        legacy.execute(
            "INSERT INTO knowledge_bases VALUES ('kb', 'Legacy', '', '2024-01-01T00:00:00+00:00')"
        )
        legacy.execute(
            """INSERT INTO documents VALUES
               ('doc', 'kb', 'old.md', 3, 'ready', NULL, '2024-01-01T00:00:00+00:00')"""
        )
        legacy.commit()
        legacy.close()

        store.initialize()

        with store.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
            self.assertIn("content_hash", columns)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            document = db.execute("SELECT filename, chunk_count FROM documents").fetchone()
            self.assertEqual(document["filename"], "old.md")
            self.assertEqual(document["chunk_count"], 3)
            knowledge_base = db.execute("SELECT updated_at FROM knowledge_bases").fetchone()
            self.assertEqual(knowledge_base["updated_at"], "2024-01-01T00:00:00+00:00")

    def test_initialize_is_idempotent_across_versions(self) -> None:
        store.initialize()
        store.initialize()
        with store.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)

    def test_connections_enable_wal_and_foreign_keys(self) -> None:
        store.initialize()
        with store.connect() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_knowledge_base_stats_aggregate_related_tables(self) -> None:
        store.initialize()
        timestamp = store.now()
        with store.connect() as db:
            db.execute(
                "INSERT INTO knowledge_bases (id, name, created_at) VALUES ('kb', 'K', ?)",
                (timestamp,),
            )
            db.execute(
                """INSERT INTO documents
                   (id, kb_id, filename, chunk_count, status, created_at)
                   VALUES ('doc-1', 'kb', 'a.md', 4, 'ready', ?),
                          ('doc-2', 'kb', 'b.md', 0, 'failed', ?)""",
                (timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO conversations VALUES ('chat', 'kb', 'T', ?, ?)",
                (timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO messages VALUES ('m1', 'chat', 'user', '你好', '[]', ?)",
                (timestamp,),
            )
            db.execute(
                "INSERT INTO pipeline_runs VALUES ('p1', 'kb', 'doc-2', 'upload', 'failed', 'x', ?)",
                (timestamp,),
            )
            stats = store.knowledge_base_stats(db, "kb")

        self.assertEqual(stats["documents"], 2)
        self.assertEqual(stats["chunks"], 4)
        self.assertEqual(stats["failed_documents"], 1)
        self.assertEqual(stats["conversations"], 1)
        self.assertEqual(stats["messages"], 1)
        self.assertEqual(stats["failed_pipeline_runs"], 1)
        self.assertEqual(stats["evaluation_runs"], 0)


if __name__ == "__main__":
    unittest.main()
