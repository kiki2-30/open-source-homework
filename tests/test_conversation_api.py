import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app import knowledge, main, store
from app.api import conversations

SAMPLE_DOCUMENT = "退款期限为 30 天。申请时请提供订单号。".encode()


class ConversationApiTests(unittest.TestCase):
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
                responses=["退款期限为 30 天。[1]"] * 8,
            ),
        )
        self.embeddings.start()
        self.chat_model.start()
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        self.kb_id = self.client.post("/api/knowledge-bases", json={"name": "会话测试"}).json()[
            "id"
        ]
        upload = self.client.post(
            f"/api/knowledge-bases/{self.kb_id}/documents",
            files=[("files", ("refund.md", SAMPLE_DOCUMENT, "text/markdown"))],
        )
        self.assertEqual(upload.status_code, 201, upload.text)

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.chat_model.stop()
        self.embeddings.stop()
        self.environment.stop()
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_follow_up_turns_receive_previous_messages(self) -> None:
        with patch.object(
            conversations, "answer_question", return_value=("退款期限为 30 天。[1]", [])
        ) as answer_question:
            first = self.client.post(
                "/api/ask", json={"kb_id": self.kb_id, "question": "第一个问题"}
            )
            conversation_id = first.json()["conversation_id"]
            self.client.post(
                "/api/ask",
                json={
                    "kb_id": self.kb_id,
                    "question": "第二个问题",
                    "conversation_id": conversation_id,
                },
            )
            self.client.post(
                "/api/ask",
                json={
                    "kb_id": self.kb_id,
                    "question": "第三个问题",
                    "conversation_id": conversation_id,
                    "history": False,
                },
            )

        self.assertEqual(answer_question.call_count, 3)
        self.assertEqual(answer_question.call_args_list[0].kwargs["history"], [])
        replay = answer_question.call_args_list[1].kwargs["history"]
        self.assertEqual([message["role"] for message in replay], ["user", "assistant"])
        self.assertEqual(replay[0]["content"], "第一个问题")
        self.assertEqual(answer_question.call_args_list[2].kwargs["history"], [])

    def test_stream_endpoint_emits_sources_tokens_and_done(self) -> None:
        with self.client.stream(
            "POST",
            "/api/ask/stream",
            json={"kb_id": self.kb_id, "question": "退款期限是多久？", "strategy": "hybrid"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            body = "".join(response.iter_text())

        self.assertIn("event: sources", body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        payloads = [
            json.loads(line[len("data: ") :])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        done = payloads[-1]
        self.assertIn("[1]", done["answer"])
        self.assertEqual(done["sources"][0]["filename"], "refund.md")
        self.assertEqual(done["sources"][0]["retrieval_strategy"], "hybrid")
        self.assertGreaterEqual(done["latency_ms"], 0)

        messages = self.client.get(f"/api/conversations/{done['conversation_id']}/messages").json()
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], done["answer"])

    def test_rename_and_paginate_conversations(self) -> None:
        with patch.object(conversations, "answer_question", return_value=("答案", [])):
            for question in ("问题一", "问题二", "问题三"):
                self.client.post("/api/ask", json={"kb_id": self.kb_id, "question": question})

        page = self.client.get(f"/api/knowledge-bases/{self.kb_id}/conversations?limit=2")
        self.assertEqual(len(page.json()), 2)
        self.assertEqual(page.headers["x-total-count"], "3")
        self.assertEqual(page.headers["x-has-more"], "true")

        conversation_id = page.json()[0]["id"]
        renamed = self.client.patch(
            f"/api/conversations/{conversation_id}", json={"title": "退款政策讨论"}
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "退款政策讨论")

        self.assertEqual(
            self.client.patch("/api/conversations/missing", json={"title": "不存在"}).status_code,
            404,
        )

    def test_ask_rejects_unknown_conversation_and_bad_strategy(self) -> None:
        response = self.client.post(
            "/api/ask",
            json={"kb_id": self.kb_id, "question": "问题", "conversation_id": "missing"},
        )
        self.assertEqual(response.status_code, 404)
        invalid = self.client.post(
            "/api/ask",
            json={"kb_id": self.kb_id, "question": "问题", "strategy": "lexical"},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_responses_carry_request_metadata(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertGreaterEqual(payload["knowledge_bases"], 1)
        self.assertTrue(response.headers["x-request-id"])
        self.assertGreaterEqual(float(response.headers["x-process-time-ms"]), 0)


if __name__ == "__main__":
    unittest.main()
