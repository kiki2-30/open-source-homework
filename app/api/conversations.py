import json
import re
import time
import uuid
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.api.common import Limit, Offset, pagination_headers, require_knowledge_base
from app.knowledge import HISTORY_TURNS, answer_question, iter_answer

router = APIRouter(tags=["conversations"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx buffers proxied responses by default, which would defeat streaming.
    "X-Accel-Buffering": "no",
}


class AskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kb_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    strategy: str = Field(default="similarity", pattern="^(similarity|mmr|hybrid)$")
    k: int | None = Field(default=None, ge=1, le=20)
    history: bool = True


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


def _start_turn(payload: AskInput) -> tuple[str, list[dict[str, Any]]]:
    """Resolve or create the conversation, then append the user message.

    Returns the conversation id plus the previous turns (oldest first) so the
    model can see the dialogue context that the new question refers to.
    """
    with store.connect() as db:
        require_knowledge_base(db, payload.kb_id)
        conversation_id = payload.conversation_id
        history: list[dict[str, Any]] = []
        if conversation_id:
            conversation = db.execute(
                "SELECT id FROM conversations WHERE id = ? AND kb_id = ?",
                (conversation_id, payload.kb_id),
            ).fetchone()
            if conversation is None:
                raise HTTPException(404, "Conversation not found in this knowledge base.")
            if payload.history and HISTORY_TURNS > 0:
                history = store.fetch_all(
                    db,
                    """SELECT role, content FROM messages WHERE conversation_id = ?
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (conversation_id, HISTORY_TURNS * 2),
                )
                history.reverse()
        else:
            conversation_id = str(uuid.uuid4())
            title = re.sub(r"\s+", " ", payload.question).strip()[:48] or "新对话"
            timestamp = store.now()
            db.execute(
                """INSERT INTO conversations (id, kb_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (conversation_id, payload.kb_id, title, timestamp, timestamp),
            )
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'user', ?, '[]', ?)",
            (str(uuid.uuid4()), conversation_id, payload.question, store.now()),
        )
    return conversation_id, history


def _finish_turn(conversation_id: str, answer: str, sources: list[dict[str, Any]]) -> None:
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


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@router.post("/api/ask")
def ask(payload: AskInput) -> dict[str, Any]:
    conversation_id, history = _start_turn(payload)
    started = time.perf_counter()
    try:
        answer, sources = answer_question(
            payload.kb_id,
            payload.question,
            payload.strategy,
            history=history,
            k=payload.k,
        )
        store.record_pipeline(
            payload.kb_id,
            "retrieve_and_generate",
            "completed",
            f"{len(sources)} sources used for question",
        )
    except Exception as exc:
        store.record_pipeline(payload.kb_id, "retrieve_and_generate", "failed", str(exc))
        raise HTTPException(502, f"Question answering failed: {exc}") from exc
    _finish_turn(conversation_id, answer, sources)
    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "sources": sources,
        "history_turns": len(history) // 2,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.post("/api/ask/stream")
def ask_stream(payload: AskInput) -> StreamingResponse:
    """Server-sent events variant of ``/api/ask``.

    Events: ``sources`` (retrieved context), ``token`` (answer deltas),
    ``done`` (final answer + metrics) and ``error``.
    """
    conversation_id, history = _start_turn(payload)
    started = time.perf_counter()

    def events() -> Iterator[bytes]:
        try:
            for event in iter_answer(
                payload.kb_id,
                payload.question,
                payload.strategy,
                history=history,
                k=payload.k,
            ):
                if event["type"] == "sources":
                    yield _sse(
                        "sources",
                        {
                            "conversation_id": conversation_id,
                            "sources": event["sources"],
                            "retrieval_strategy": event["retrieval_strategy"],
                            "history_turns": len(history) // 2,
                        },
                    )
                elif event["type"] == "token":
                    yield _sse("token", {"text": event["text"]})
                else:
                    _finish_turn(conversation_id, event["answer"], event["sources"])
                    store.record_pipeline(
                        payload.kb_id,
                        "retrieve_and_generate",
                        "completed",
                        f"{len(event['sources'])} sources used for question (stream)",
                    )
                    yield _sse(
                        "done",
                        {
                            "conversation_id": conversation_id,
                            "answer": event["answer"],
                            "sources": event["sources"],
                            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        },
                    )
        except Exception as exc:
            store.record_pipeline(payload.kb_id, "retrieve_and_generate", "failed", str(exc))
            yield _sse("error", {"message": str(exc), "conversation_id": conversation_id})

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/api/knowledge-bases/{kb_id}/conversations")
def conversations(kb_id: str, response: Response, limit: Limit = 100, offset: Offset = 0):
    with store.connect() as db:
        require_knowledge_base(db, kb_id)
        total = (
            store.scalar(db, "SELECT COUNT(*) FROM conversations WHERE kb_id = ?", (kb_id,)) or 0
        )
        items = store.fetch_all(
            db,
            """SELECT * FROM conversations WHERE kb_id = ?
               ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            (kb_id, limit, offset),
        )
    response.headers.update(pagination_headers(int(total), limit, offset))
    return items


@router.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, payload: ConversationUpdate) -> dict[str, Any]:
    with store.connect() as db:
        cursor = db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (payload.title, store.now(), conversation_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Conversation not found.")
        renamed = store.fetch_one(
            db, "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
    return renamed or {}


@router.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str, response: Response, limit: Limit = 200, offset: Offset = 0
):
    with store.connect() as db:
        conversation = db.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
        total = (
            store.scalar(
                db, "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
            )
            or 0
        )
        messages = store.fetch_all(
            db,
            """SELECT * FROM messages WHERE conversation_id = ?
               ORDER BY created_at, rowid LIMIT ? OFFSET ?""",
            (conversation_id, limit, offset),
        )
    for message in messages:
        message["sources"] = store.parse_sources(message["sources"])
    response.headers.update(pagination_headers(int(total), limit, offset))
    return messages


@router.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    with store.connect() as db:
        cursor = db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Conversation not found.")
