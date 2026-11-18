import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import require_knowledge_base
from app.knowledge import answer_question

router = APIRouter(tags=["conversations"])


class AskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kb_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    strategy: str = Field(default="similarity", pattern="^(similarity|mmr)$")


@router.post("/api/ask")
def ask(payload: AskInput) -> dict[str, Any]:
    with store.connect() as db:
        require_knowledge_base(db, payload.kb_id)
        conversation_id = payload.conversation_id
        if conversation_id:
            conversation = db.execute(
                "SELECT id FROM conversations WHERE id = ? AND kb_id = ?",
                (conversation_id, payload.kb_id),
            ).fetchone()
            if conversation is None:
                raise HTTPException(404, "Conversation not found in this knowledge base.")
        else:
            conversation_id = str(uuid.uuid4())
            title = re.sub(r"\s+", " ", payload.question).strip()[:48]
            db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                (conversation_id, payload.kb_id, title, store.now(), store.now()),
            )
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'user', ?, '[]', ?)",
            (str(uuid.uuid4()), conversation_id, payload.question, store.now()),
        )
    try:
        answer, sources = answer_question(payload.kb_id, payload.question, payload.strategy)
        store.record_pipeline(
            payload.kb_id,
            "retrieve_and_generate",
            "completed",
            f"{len(sources)} sources used for question",
        )
    except Exception as exc:
        store.record_pipeline(payload.kb_id, "retrieve_and_generate", "failed", str(exc))
        raise HTTPException(502, f"Question answering failed: {exc}") from exc
    with store.connect() as db:
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?)",
            (
                str(uuid.uuid4()),
                conversation_id,
                answer,
                json.dumps(sources, ensure_ascii=False),
                store.now(),
            ),
        )
        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (store.now(), conversation_id)
        )
    return {"conversation_id": conversation_id, "answer": answer, "sources": sources}


@router.get("/api/knowledge-bases/{kb_id}/conversations")
def conversations(kb_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        return store.rows_as_dicts(
            db.execute(
                "SELECT * FROM conversations WHERE kb_id = ? ORDER BY updated_at DESC",
                (kb_id,),
            ).fetchall()
        )


@router.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str) -> list[dict[str, Any]]:
    with store.connect() as db:
        conversation = db.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
        messages = store.rows_as_dicts(
            db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        )
    for message in messages:
        message["sources"] = store.parse_sources(message["sources"])
    return messages


@router.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    with store.connect() as db:
        cursor = db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Conversation not found.")
