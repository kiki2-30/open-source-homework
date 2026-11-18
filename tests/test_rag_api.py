import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app import knowledge, main, store


class RagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_data_dir = store.DATA_DIR
        self.previous_db_path = store.DB_PATH
        self.environment = patch.dict(os.environ, {"DATA_DIR": self.temp_dir.name})
        self.environment.start()
        store.DATA_DIR = Path(self.temp_dir.name)
        store.DB_PATH = store.DATA_DIR / "test.sqlite3"
        (store.DATA_DIR / "uploads").mkdir()
        self.embeddings = patch.object(
            knowledge, "configured_embeddings", return_value=FakeEmbeddings(size=16)
        )
        self.chat_model = patch.object(
            knowledge,
            "configured_chat_model",
            return_value=FakeListChatModel(
                responses=[
                    "退款期限为 30 天。[1]",
                    "退款期限为 30 天。[1]",
                    "退款期限为 30 天。[1]",
                ]
            ),
        )
        self.embeddings.start()
        self.chat_model.start()

    def tearDown(self) -> None:
        self.chat_model.stop()
        self.embeddings.stop()
        self.environment.stop()
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_upload_chat_citation_evaluation_and_cleanup(self) -> None:
        with TestClient(main.app) as client:
            knowledge_base = client.post("/api/knowledge-bases", json={"name": "退款政策"}).json()
            kb_id = knowledge_base["id"]
            upload = client.post(
                f"/api/knowledge-bases/{kb_id}/documents",
                files=[
                    (
                        "files",
                        (
                            "refund.md",
                            "退款期限为 30 天。申请时请提供订单号。".encode(),
                            "text/markdown",
                        ),
                    )
                ],
            )
            self.assertEqual(upload.status_code, 201)
            self.assertEqual(upload.json()[0]["status"], "ready")

            response = client.post(
                "/api/ask", json={"kb_id": kb_id, "question": "退款期限是多久？"}
            )
            self.assertEqual(response.status_code, 200)
            answer = response.json()
            self.assertIn("[1]", answer["answer"])
            self.assertEqual(answer["sources"][0]["filename"], "refund.md")
            self.assertGreaterEqual(answer["sources"][0]["score"], 0)
            self.assertLessEqual(answer["sources"][0]["score"], 1)

            diverse_answer = client.post(
                "/api/ask",
                json={
                    "kb_id": kb_id,
                    "question": "退款期限是多久？",
                    "strategy": "mmr",
                },
            )
            self.assertEqual(diverse_answer.status_code, 200, diverse_answer.text)
            self.assertEqual(diverse_answer.json()["sources"][0]["retrieval_strategy"], "mmr")

            messages = client.get(f"/api/conversations/{answer['conversation_id']}/messages").json()
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1]["sources"][0]["filename"], "refund.md")

            evaluation = client.post(
                "/api/evaluate",
                json={"kb_id": kb_id, "question": "退款期限？", "expected": "30 天"},
            )
            self.assertEqual(evaluation.status_code, 201)
            self.assertTrue(evaluation.json()["exact_substring_hit"])

            document_id = upload.json()[0]["id"]
            self.assertEqual(
                client.delete(f"/api/knowledge-bases/{kb_id}/documents/{document_id}").status_code,
                204,
            )
            self.assertEqual(client.get(f"/api/knowledge-bases/{kb_id}/documents").json(), [])
            self.assertEqual(client.delete(f"/api/knowledge-bases/{kb_id}").status_code, 204)


if __name__ == "__main__":
    unittest.main()
