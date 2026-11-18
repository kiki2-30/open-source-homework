import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import require_knowledge_base
from app.knowledge import answer_question

router = APIRouter(tags=["operations"])


class EvaluationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kb_id: str
    question: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=4000)


@router.get("/api/knowledge-bases/{kb_id}/pipeline")
def pipeline_runs(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        return store.rows_as_dicts(
            db.execute(
                "SELECT * FROM pipeline_runs WHERE kb_id = ? ORDER BY created_at DESC LIMIT 100",
                (kb_id,),
            ).fetchall()
        )


@router.post("/api/evaluate", status_code=201)
def evaluate(payload: EvaluationInput) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, payload.kb_id)
    try:
        answer, sources = answer_question(payload.kb_id, payload.question)
    except Exception as exc:
        raise HTTPException(502, f"Evaluation run failed: {exc}") from exc
    hit = int(payload.expected.casefold() in answer.casefold())
    with store.connect() as db:
        db.execute(
            "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                payload.kb_id,
                payload.question,
                payload.expected,
                answer,
                hit,
                len(sources),
                store.now(),
            ),
        )
        total, hits = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(hit), 0) FROM evaluations WHERE kb_id = ?",
            (payload.kb_id,),
        ).fetchone()
    return {
        "answer": answer,
        "sources": sources,
        "exact_substring_hit": bool(hit),
        "citation_count": len(sources),
        "aggregate": {
            "cases": total,
            "substring_hit_rate": round(hits / total, 4) if total else 0,
        },
    }


@router.get("/api/knowledge-bases/{kb_id}/evaluations")
def evaluation_history(kb_id: str) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        rows = store.rows_as_dicts(
            db.execute(
                "SELECT * FROM evaluations WHERE kb_id = ? ORDER BY created_at DESC LIMIT 100",
                (kb_id,),
            ).fetchall()
        )
        total, hits = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(hit), 0) FROM evaluations WHERE kb_id = ?",
            (kb_id,),
        ).fetchone()
    return {
        "items": rows,
        "aggregate": {
            "cases": total,
            "substring_hit_rate": round(hits / total, 4) if total else 0,
        },
    }
