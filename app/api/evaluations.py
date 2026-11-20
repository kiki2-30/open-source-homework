import json
import time
import uuid
from statistics import mean
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import require_knowledge_base
from app.knowledge import answer_question

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/evaluations", tags=["evaluations"])
MAX_BATCH_CASES = 25


class EvaluationCaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    expected_answer: str = Field(min_length=1, max_length=4000)
    expected_sources: list[str] = Field(default_factory=list, max_length=20)


class EvaluationBatchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    dataset_name: str = Field(default="evaluation-dataset", min_length=1, max_length=100)
    strategy: str = Field(default="similarity", pattern="^(similarity|mmr|hybrid)$")
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=MAX_BATCH_CASES)


def _source_metrics(
    expected_sources: list[str], retrieved_sources: list[dict[str, Any]]
) -> tuple[bool | None, float | None, float | None, float | None]:
    if not expected_sources:
        return None, None, None, None
    expected = {source.strip().casefold() for source in expected_sources if source.strip()}
    if not expected:
        return None, None, None, None
    retrieved_names = list(
        dict.fromkeys(
            str(source.get("filename", "")).strip().casefold()
            for source in retrieved_sources
            if source.get("filename")
        )
    )
    matched = sum(name in expected for name in retrieved_names)
    return (
        matched > 0,
        matched / len(retrieved_names) if retrieved_names else 0.0,
        matched / len(expected),
        next((1 / rank for rank, name in enumerate(retrieved_names, 1) if name in expected), 0.0),
    )


def _aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [case for case in cases if case["status"] == "completed"]
    source_cases = [case for case in successful if case["source_hit"] is not None]
    return {
        "total_cases": len(cases),
        "successful_cases": len(successful),
        "failed_cases": len(cases) - len(successful),
        "answer_hit_rate": (
            round(mean(case["answer_hit"] for case in successful), 4) if successful else 0.0
        ),
        "source_evaluated_cases": len(source_cases),
        "source_hit_rate": (
            round(mean(float(case["source_hit"]) for case in source_cases), 4)
            if source_cases
            else None
        ),
        "precision_at_k": (
            round(mean(case["source_precision"] for case in source_cases), 4)
            if source_cases
            else None
        ),
        "recall_at_k": (
            round(mean(case["source_recall"] for case in source_cases), 4) if source_cases else None
        ),
        "mrr": (
            round(mean(case["reciprocal_rank"] for case in source_cases), 4)
            if source_cases
            else None
        ),
        "average_latency_ms": (
            round(mean(case["latency_ms"] for case in successful), 2) if successful else None
        ),
    }


@router.post("/batch", status_code=201)
def run_batch_evaluation(kb_id: str, payload: EvaluationBatchInput) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)

    run_id = str(uuid.uuid4())
    created_at = store.now()
    with store.connect() as db:
        db.execute(
            "INSERT INTO evaluation_runs VALUES (?, ?, ?, 'running', '{}', ?)",
            (run_id, kb_id, payload.dataset_name, created_at),
        )

    results: list[dict[str, Any]] = []
    for position, case in enumerate(payload.cases, 1):
        case_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            answer, sources = answer_question(kb_id, case.question, payload.strategy)
            latency_ms = (time.perf_counter() - started) * 1000
            answer_hit = case.expected_answer.casefold() in answer.casefold()
            source_hit, source_precision, source_recall, reciprocal_rank = _source_metrics(
                case.expected_sources, sources
            )
            result = {
                "id": case_id,
                "position": position,
                "question": case.question,
                "expected_answer": case.expected_answer,
                "expected_sources": case.expected_sources,
                "answer": answer,
                "retrieved_sources": sources,
                "answer_hit": answer_hit,
                "source_hit": source_hit,
                "source_precision": source_precision,
                "source_recall": source_recall,
                "reciprocal_rank": reciprocal_rank,
                "latency_ms": round(latency_ms, 2),
                "status": "completed",
                "error": None,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            result = {
                "id": case_id,
                "position": position,
                "question": case.question,
                "expected_answer": case.expected_answer,
                "expected_sources": case.expected_sources,
                "answer": "",
                "retrieved_sources": [],
                "answer_hit": None,
                "source_hit": None,
                "source_precision": None,
                "source_recall": None,
                "reciprocal_rank": None,
                "latency_ms": round(latency_ms, 2),
                "status": "failed",
                "error": str(exc),
            }
        with store.connect() as db:
            db.execute(
                """INSERT INTO evaluation_cases
                   (id, run_id, position, question, expected_answer, expected_sources, answer,
                    retrieved_sources, answer_hit, source_hit, source_precision, source_recall,
                    reciprocal_rank, latency_ms, status, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case_id,
                    run_id,
                    position,
                    case.question,
                    case.expected_answer,
                    json.dumps(case.expected_sources, ensure_ascii=False),
                    result["answer"],
                    json.dumps(result["retrieved_sources"], ensure_ascii=False),
                    None if result["answer_hit"] is None else int(result["answer_hit"]),
                    None if result["source_hit"] is None else int(result["source_hit"]),
                    result["source_precision"],
                    result["source_recall"],
                    result["reciprocal_rank"],
                    result["latency_ms"],
                    result["status"],
                    result["error"],
                    store.now(),
                ),
            )
        results.append(result)

    metrics = _aggregate(results)
    status = (
        "completed"
        if metrics["failed_cases"] == 0
        else ("failed" if metrics["successful_cases"] == 0 else "partial")
    )
    with store.connect() as db:
        db.execute(
            "UPDATE evaluation_runs SET status = ?, metrics = ? WHERE id = ?",
            (status, json.dumps(metrics), run_id),
        )
    store.record_pipeline(
        kb_id,
        "batch_evaluation",
        status,
        f"{payload.dataset_name}: {metrics['successful_cases']}/{metrics['total_cases']} cases",
    )
    return {
        "id": run_id,
        "knowledge_base_id": kb_id,
        "dataset_name": payload.dataset_name,
        "strategy": payload.strategy,
        "status": status,
        "metrics": metrics,
        "cases": results,
        "created_at": created_at,
    }


@router.get("/runs")
def list_evaluation_runs(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        runs = store.rows_as_dicts(
            db.execute(
                """SELECT id, kb_id, dataset_name, status, metrics, created_at
                   FROM evaluation_runs WHERE kb_id = ? ORDER BY created_at DESC LIMIT 50""",
                (kb_id,),
            ).fetchall()
        )
    for run in runs:
        run["metrics"] = json.loads(run["metrics"])
    return runs


@router.get("/runs/{run_id}")
def get_evaluation_run(kb_id: str, run_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        run = db.execute(
            "SELECT * FROM evaluation_runs WHERE id = ? AND kb_id = ?", (run_id, kb_id)
        ).fetchone()
        if run is None:
            raise HTTPException(404, "Evaluation run not found.")
        cases = store.rows_as_dicts(
            db.execute(
                "SELECT * FROM evaluation_cases WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        )
    for case in cases:
        case["expected_sources"] = json.loads(case["expected_sources"])
        case["retrieved_sources"] = json.loads(case["retrieved_sources"])
        if case["answer_hit"] is not None:
            case["answer_hit"] = bool(case["answer_hit"])
        if case["source_hit"] is not None:
            case["source_hit"] = bool(case["source_hit"])
    return {
        **dict(run),
        "metrics": json.loads(run["metrics"]),
        "cases": cases,
    }
