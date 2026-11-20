import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.embeddings import FakeEmbeddings

from app import knowledge, main, store

SAMPLE_DOCUMENT = "退款期限为 30 天。申请时请提供订单号。".encode()


class DocumentManagementTests(unittest.TestCase):
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
        self.embeddings.start()
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        self.kb_id = self.client.post(
            "/api/knowledge-bases", json={"name": "资料管理", "description": "初始描述"}
        ).json()["id"]

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.embeddings.stop()
        self.environment.stop()
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def upload(self, filename: str, payload: bytes = SAMPLE_DOCUMENT) -> dict:
        response = self.client.post(
            f"/api/knowledge-bases/{self.kb_id}/documents",
            files=[("files", (filename, payload, "text/markdown"))],
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()[0]

    def test_identical_content_is_not_indexed_twice(self) -> None:
        first = self.upload("refund.md")
        duplicate = self.upload("refund-copy.md")

        self.assertEqual(first["status"], "ready")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["duplicate_of"], "refund.md")
        documents = self.client.get(f"/api/knowledge-bases/{self.kb_id}/documents").json()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["chunk_count"], first["chunk_count"])

    def test_knowledge_base_can_be_renamed_and_reports_stats(self) -> None:
        document = self.upload("refund.md")

        renamed = self.client.patch(
            f"/api/knowledge-bases/{self.kb_id}",
            json={"name": "退款政策", "description": "客服使用"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "退款政策")

        stats = self.client.get(f"/api/knowledge-bases/{self.kb_id}/stats").json()
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["chunks"], document["chunk_count"])
        self.assertEqual(stats["conversations"], 0)
        self.assertEqual(stats["failed_pipeline_runs"], 0)

        detail = self.client.get(f"/api/knowledge-bases/{self.kb_id}").json()
        self.assertEqual(detail["description"], "客服使用")
        self.assertEqual(detail["stats"]["documents"], 1)

    def test_document_detail_lists_pipeline_stages(self) -> None:
        document = self.upload("refund.md")
        detail = self.client.get(
            f"/api/knowledge-bases/{self.kb_id}/documents/{document['id']}"
        ).json()
        self.assertEqual(detail["filename"], "refund.md")
        self.assertEqual(
            [run["stage"] for run in detail["pipeline"]], ["upload", "parse_and_index"]
        )

    def test_reindex_rebuilds_vectors_and_records_the_new_run(self) -> None:
        document = self.upload("refund.md")

        response = self.client.post(
            f"/api/knowledge-bases/{self.kb_id}/documents/{document['id']}/reindex"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["chunk_count"], document["chunk_count"])

        pipeline = self.client.get(f"/api/knowledge-bases/{self.kb_id}/pipeline").json()
        self.assertIn("reindex", [run["stage"] for run in pipeline])
        self.assertEqual(pipeline[0]["status"], "completed")

    def test_missing_resources_return_404(self) -> None:
        self.assertEqual(self.client.get("/api/knowledge-bases/missing").status_code, 404)
        self.assertEqual(self.client.get("/api/knowledge-bases/missing/stats").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/knowledge-bases/{self.kb_id}/documents/missing").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/knowledge-bases/{self.kb_id}/documents/missing/reindex"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.patch("/api/knowledge-bases/missing", json={"name": "x"}).status_code,
            404,
        )

    def test_upload_rejects_unsupported_extension(self) -> None:
        response = self.client.post(
            f"/api/knowledge-bases/{self.kb_id}/documents",
            files=[("files", ("archive.zip", b"PK\x03\x04", "application/zip"))],
        )
        self.assertEqual(response.status_code, 415)

    def test_documents_can_be_filtered_and_paginated(self) -> None:
        self.upload("refund.md")
        self.upload("faq.md", "常见问题：如何查询订单？".encode())

        page = self.client.get(f"/api/knowledge-bases/{self.kb_id}/documents?limit=1")
        self.assertEqual(len(page.json()), 1)
        self.assertEqual(page.headers["x-total-count"], "2")
        self.assertEqual(page.headers["x-has-more"], "true")

        ready = self.client.get(f"/api/knowledge-bases/{self.kb_id}/documents?status=ready")
        self.assertEqual(len(ready.json()), 2)
        self.assertEqual(
            self.client.get(
                f"/api/knowledge-bases/{self.kb_id}/documents?status=bogus"
            ).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
