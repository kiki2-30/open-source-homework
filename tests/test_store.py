import json
import tempfile
import unittest
from pathlib import Path

from app import store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_data_dir = store.DATA_DIR
        self.previous_db_path = store.DB_PATH
        store.DATA_DIR = Path(self.temp_dir.name)
        store.DB_PATH = store.DATA_DIR / "test.sqlite3"
        store.initialize()

    def tearDown(self) -> None:
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_initialization_is_idempotent_and_cascades_related_rows(self) -> None:
        store.initialize()
        with store.connect() as db:
            db.execute(
                "INSERT INTO knowledge_bases (id, name, created_at) VALUES ('kb', 'Test', ?)",
                (store.now(),),
            )
            db.execute(
                "INSERT INTO conversations VALUES ('chat', 'kb', 'Question', ?, ?)",
                (store.now(), store.now()),
            )
            db.execute(
                "INSERT INTO messages VALUES ('message', 'chat', 'user', 'Hello', '[]', ?)",
                (store.now(),),
            )
            db.execute(
                "INSERT INTO pipeline_runs VALUES ('run', 'kb', NULL, 'upload', 'completed', 'ok', ?)",
                (store.now(),),
            )
            db.execute("DELETE FROM knowledge_bases WHERE id = 'kb'")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0], 0)

    def test_parse_sources_returns_json_list(self) -> None:
        sources = [{"index": 1, "filename": "guide.pdf"}]
        self.assertEqual(store.parse_sources(json.dumps(sources)), sources)


if __name__ == "__main__":
    unittest.main()
