import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main, store
from app.api import evaluations
from app.api.evaluations import EvaluationBatchInput


class BatchEvaluationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_data_dir = store.DATA_DIR
        self.previous_db_path = store.DB_PATH
        self.environment = patch.dict(os.environ, {"DATA_DIR": self.temp_dir.name})
        self.environment.start()
        store.DATA_DIR = Path(self.temp_dir.name)
        store.DB_PATH = store.DATA_DIR / "test.sqlite3"
        (store.DATA_DIR / "uploads").mkdir()
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        response = self.client.post("/api/knowledge-bases", json={"name": "Evaluation"})
        self.kb_id = response.json()["id"]

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        store.DATA_DIR = self.previous_data_dir
        store.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_batch_metrics_persist_cases_and_surface_partial_failures(self) -> None:
        sources = [
            {"filename": "other.pdf", "index": 1},
            {"filename": "target.pdf", "index": 2},
            {"filename": "target.pdf", "index": 3},
        ]
        with patch.object(
            evaluations,
            "answer_question",
            side_effect=[
                ("Contains the expected phrase.", sources),
                ("A different answer.", []),
                RuntimeError("model unavailable"),
            ],
        ) as answer_question:
            response = self.client.post(
                f"/api/knowledge-bases/{self.kb_id}/evaluations/batch",
                json={
                    "dataset_name": "citation-quality",
                    "strategy": "mmr",
                    "cases": [
                        {
                            "question": "first",
                            "expected_answer": "expected phrase",
                            "expected_sources": ["target.pdf", "missing.pdf"],
                        },
                        {"question": "second", "expected_answer": "expected"},
                        {
                            "question": "third",
                            "expected_answer": "anything",
                            "expected_sources": ["target.pdf"],
                        },
                    ],
                },
            )

        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["metrics"]["successful_cases"], 2)
        self.assertEqual(result["metrics"]["failed_cases"], 1)
        self.assertEqual(result["metrics"]["answer_hit_rate"], 0.5)
        self.assertEqual(result["metrics"]["precision_at_k"], 0.5)
        self.assertEqual(result["metrics"]["recall_at_k"], 0.5)
        self.assertEqual(result["metrics"]["mrr"], 0.5)
        self.assertEqual(result["cases"][2]["error"], "model unavailable")
        self.assertEqual(answer_question.call_args_list[0].args[2], "mmr")

        run_id = result["id"]
        details = self.client.get(f"/api/knowledge-bases/{self.kb_id}/evaluations/runs/{run_id}")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(len(details.json()["cases"]), 3)
        self.assertEqual(details.json()["cases"][0]["source_precision"], 0.5)
        history = self.client.get(f"/api/knowledge-bases/{self.kb_id}/evaluations/runs")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()[0]["id"], run_id)

    def test_batch_limit_and_retrieval_strategy_are_validated(self) -> None:
        too_many_cases = {"cases": [{"question": "q", "expected_answer": "a"} for _ in range(26)]}
        response = self.client.post(
            f"/api/knowledge-bases/{self.kb_id}/evaluations/batch",
            json=too_many_cases,
        )
        self.assertEqual(response.status_code, 422)

    def test_bundled_demo_dataset_matches_batch_api_schema(self) -> None:
        dataset_path = Path(__file__).parents[1] / "examples" / "evaluation" / "refund-policy.jsonl"
        cases = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        payload = EvaluationBatchInput.model_validate(
            {"dataset_name": "refund-policy", "cases": cases}
        )
        self.assertEqual(len(payload.cases), 5)
        self.assertTrue(
            all(case.expected_sources == ["refund-policy.md"] for case in payload.cases)
        )


if __name__ == "__main__":
    unittest.main()
