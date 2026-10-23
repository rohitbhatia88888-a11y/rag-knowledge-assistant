import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import Principal, current_principal
from app.db import AsyncSession, ChatSession, Message, SessionLocal, get_db
from app.generation.answer import extract_citations, is_idk, rewrite_query, stream_answer
from app.generation.prompts import IDK_ANSWER
from app.ratelimit import rate_limit
from app.retrieval.pipeline import retrieve
from app.retrieval.store import RetrievedChunk
from app.services import get_llm, get_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: uuid.UUID | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sources: list | None
    cited_chunk_ids: list | None
    created_at: datetime
    model_config = {"from_attributes": True}


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def source_view(i: int, c: RetrievedChunk) -> dict:
    return {
        "n": i,
        "chunk_id": c.id,
        "document_id": c.document_id,
        "document_title": c.document_title,
        "headings": c.headings,
        "page_start": c.page_start,
        "page_end": c.page_end,
        "text": c.text,
        "score": round(c.score, 4),
    }


async def _owned_session(db: AsyncSession, session_id: uuid.UUID, principal: Principal) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != principal.user_id or session.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Session not found")
    return session


@router.post("")
async def chat(body: ChatRequest, principal: Principal = Depends(rate_limit), db: AsyncSession = Depends(get_db)):
    """Answer a question as a Server-Sent Events stream.

    Events: `session` -> `sources` -> `token`* -> `done` (or `error`).
    """
    if body.session_id:
        session = await _owned_session(db, body.session_id, principal)
    else:
        session = ChatSession(tenant_id=principal.tenant_id, user_id=principal.user_id, title=body.question[:80])
        db.add(session)
        await db.commit()

    history_rows = await db.scalars(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at.desc()).limit(6)
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(list(history_rows))]
    db.add(Message(session_id=session.id, role="user", content=body.question))
    await db.commit()

    return StreamingResponse(
        _answer_stream(session.id, body.question, history, principal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _answer_stream(
    session_id: uuid.UUID, question: str, history: list[dict], principal: Principal
) -> AsyncIterator[str]:
    started = time.perf_counter()
    yield sse("session", {"session_id": str(session_id)})
    llm = get_llm()
    try:
        search_query = await rewrite_query(llm, question, history)
        chunks = await retrieve(get_store(), principal, search_query)
        sources = [source_view(i, c) for i, c in enumerate(chunks, start=1)]
        yield sse("sources", {"search_query": search_query, "sources": sources})

        if not chunks:
            answer = IDK_ANSWER
            yield sse("token", {"text": answer})
        elif llm is None:
            yield sse("error", {"message": "No LLM configured (set OPENAI_API_KEY). The retrieved sources are listed below."})
            return
        else:
            parts: list[str] = []
            async for delta in stream_answer(llm, question, chunks):
                parts.append(delta)
                yield sse("token", {"text": delta})
            answer = "".join(parts)
    except Exception:
        log.exception("chat failed")
        yield sse("error", {"message": "Something went wrong while answering. Please try again."})
        return

    valid, invalid = extract_citations(answer, len(chunks))
    if invalid:
        log.warning("answer cited non-existent sources %s", invalid)
    cited_ids = [] if is_idk(answer) else [chunks[n - 1].id for n in valid]
    latency_ms = int((time.perf_counter() - started) * 1000)

    async with SessionLocal() as db:
        msg = Message(
            session_id=session_id,
            role="assistant",
            content=answer,
            search_query=search_query,
            sources=[{k: v for k, v in s.items() if k != "text"} for s in sources],
            cited_chunk_ids=cited_ids,
            latency_ms=latency_ms,
        )
        db.add(msg)
        await db.commit()

    yield sse(
        "done",
        {"message_id": str(msg.id), "cited": valid, "invalid_citations": invalid, "latency_ms": latency_ms},
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == principal.user_id, ChatSession.tenant_id == principal.tenant_id)
        .order_by(ChatSession.created_at.desc())
        .limit(50)
    )
    return list(rows)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: uuid.UUID, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
):
    await _owned_session(db, session_id, principal)
    rows = await db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))
    return list(rows)
