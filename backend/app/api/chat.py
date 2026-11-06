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
from app.tracing import trace, tracing_enabled

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


async def _start_turn(
    db: AsyncSession, body: ChatRequest, principal: Principal
) -> tuple[uuid.UUID, list[dict]]:
    """Resolve or create the session, record the user's question, and return the prior history."""
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
    return session.id, history


@router.post("")
async def chat(body: ChatRequest, principal: Principal = Depends(rate_limit), db: AsyncSession = Depends(get_db)):
    """Answer a question as a Server-Sent Events stream.

    Events: `session` -> `sources` -> `token`* -> `done` (or `error`).
    """
    session_id, history = await _start_turn(db, body, principal)

    async def stream() -> AsyncIterator[str]:
        async for event, data in _answer_events(session_id, body.question, history, principal):
            yield sse(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/answer")
async def chat_answer(
    body: ChatRequest, principal: Principal = Depends(rate_limit), db: AsyncSession = Depends(get_db)
) -> dict:
    """Answer a question as a single JSON response, for clients that can't read SSE (e.g. Power Platform connectors)."""
    session_id, history = await _start_turn(db, body, principal)
    return await collect_answer(_answer_events(session_id, body.question, history, principal))


async def collect_answer(events: AsyncIterator[tuple[str, dict]]) -> dict:
    """Drain the event stream into one response body; an `error` event becomes an HTTP error."""
    out: dict = {"session_id": None, "search_query": None, "sources": []}
    tokens: list[str] = []
    async for event, data in events:
        if event == "session":
            out["session_id"] = data["session_id"]
        elif event == "sources":
            out["search_query"] = data["search_query"]
            out["sources"] = [{k: v for k, v in s.items() if k != "text"} for s in data["sources"]]
        elif event == "token":
            tokens.append(data["text"])
        elif event == "error":
            raise HTTPException(503, data["message"])
        elif event == "done":
            out.update(data)
    out["answer"] = "".join(tokens)
    return out


async def _answer_events(
    session_id: uuid.UUID, question: str, history: list[dict], principal: Principal
) -> AsyncIterator[tuple[str, dict]]:
    started = time.perf_counter()
    yield "session", {"session_id": str(session_id)}
    llm = get_llm()
    with trace(
        "chat",
        user_id=f"{principal.tenant_id}/{principal.user_id}",
        session_id=str(session_id),
        tags=[principal.tenant_id],
        input=question,
    ) as root:
        try:
            search_query = await rewrite_query(llm, question, history)
            chunks = await retrieve(get_store(), principal, search_query)
            sources = [source_view(i, c) for i, c in enumerate(chunks, start=1)]
            yield "sources", {"search_query": search_query, "sources": sources}

            if not chunks:
                answer = IDK_ANSWER
                yield "token", {"text": answer}
            elif llm is None:
                yield "error", {"message": "No LLM configured (set OPENAI_API_KEY). The retrieved sources are listed below."}
                return
            else:
                parts: list[str] = []
                async for delta in stream_answer(llm, question, chunks):
                    parts.append(delta)
                    yield "token", {"text": delta}
                answer = "".join(parts)
        except Exception as e:
            log.exception("chat failed")
            root.update(level="ERROR", status_message=str(e)[:500])
            yield "error", {"message": "Something went wrong while answering. Please try again."}
            return

        valid, invalid = extract_citations(answer, len(chunks))
        if invalid:
            log.warning("answer cited non-existent sources %s", invalid)
        idk = is_idk(answer)
        cited_ids = [] if idk else [chunks[n - 1].id for n in valid]
        latency_ms = int((time.perf_counter() - started) * 1000)
        root.update(
            output=answer,
            metadata={"idk": idk, "cited": valid, "invalid_citations": invalid, "latency_ms": latency_ms},
        )
        trace_id = root.trace_id

    async with SessionLocal() as db:
        msg = Message(
            session_id=session_id,
            role="assistant",
            content=answer,
            search_query=search_query,
            sources=[{k: v for k, v in s.items() if k != "text"} for s in sources],
            cited_chunk_ids=cited_ids,
            latency_ms=latency_ms,
            trace_id=trace_id if tracing_enabled() else None,
        )
        db.add(msg)
        await db.commit()

    yield "done", {
        "message_id": str(msg.id), "idk": idk, "cited": valid, "invalid_citations": invalid, "latency_ms": latency_ms,
    }


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
