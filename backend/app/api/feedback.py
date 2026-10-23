import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import Principal, current_principal
from app.db import AsyncSession, ChatSession, Feedback, Message, get_db
from app.tracing import score

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: Literal[1, -1]
    comment: str | None = Field(None, max_length=2000)


@router.post("", status_code=201)
async def submit(body: FeedbackIn, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    """Thumbs up/down, stored against the assistant message (which records the question's chunks)."""
    msg = await db.get(Message, body.message_id)
    session = await db.get(ChatSession, msg.session_id) if msg else None
    if msg is None or msg.role != "assistant" or session is None or session.user_id != principal.user_id:
        raise HTTPException(404, "Message not found")
    fb = Feedback(message_id=msg.id, user_id=principal.user_id, rating=body.rating, comment=body.comment)
    db.add(fb)
    await db.commit()
    # Attach the thumbs up/down to the answer's trace so weak answers can be filtered in Langfuse.
    score(msg.trace_id, "user_feedback", body.rating, body.comment)
    return {"id": str(fb.id)}
