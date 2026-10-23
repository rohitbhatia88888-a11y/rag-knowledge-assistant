import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.config import get_settings
from app.generation.prompts import IDK_ANSWER, REWRITE_SYSTEM, answer_messages
from app.retrieval.store import RetrievedChunk
from app.tracing import observation

CITATION_RE = re.compile(r"\[(\d+)\]")


async def rewrite_query(llm: AsyncOpenAI | None, question: str, history: list[dict]) -> str:
    """Turn a follow-up like "what about contractors?" into a standalone search query."""
    if llm is None or not history:
        return question
    convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-6:])
    with observation("rewrite-query", input=question) as obs:
        resp = await llm.chat.completions.create(
            model=get_settings().llm_model,
            temperature=0,
            max_tokens=100,
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM},
                {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {question}"},
            ],
        )
        rewritten = (resp.choices[0].message.content or question).strip()
        obs.update(output=rewritten)
        return rewritten


async def stream_answer(llm: AsyncOpenAI, question: str, chunks: list[RetrievedChunk]) -> AsyncIterator[str]:
    with observation("answer", input=question) as obs:
        stream = await llm.chat.completions.create(
            model=get_settings().llm_model,
            temperature=0.1,
            max_tokens=get_settings().answer_max_tokens,
            stream=True,
            messages=answer_messages(question, chunks),
        )
        parts: list[str] = []
        async for event in stream:
            if event.choices and (delta := event.choices[0].delta.content):
                parts.append(delta)
                yield delta
        obs.update(output="".join(parts))


async def generate_answer(llm: AsyncOpenAI, question: str, chunks: list[RetrievedChunk]) -> str:
    return "".join([delta async for delta in stream_answer(llm, question, chunks)])


def extract_citations(answer: str, n_sources: int) -> tuple[list[int], list[int]]:
    """Return (valid, invalid) 1-based source numbers cited in the answer, in first-seen order."""
    valid, invalid = [], []
    for m in CITATION_RE.finditer(answer):
        n = int(m.group(1))
        bucket = valid if 1 <= n <= n_sources else invalid
        if n not in bucket:
            bucket.append(n)
    return valid, invalid


def is_idk(answer: str) -> bool:
    """True if the answer is a refusal. Contains-check, since models sometimes add a sentence around it."""
    return IDK_ANSWER.rstrip(".").lower() in answer.lower()
