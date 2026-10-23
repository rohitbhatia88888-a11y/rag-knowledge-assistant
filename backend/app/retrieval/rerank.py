import asyncio
from collections import Counter
from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import get_settings
from app.retrieval.store import RetrievedChunk


@lru_cache
def _encoder() -> TextCrossEncoder:
    return TextCrossEncoder(get_settings().reranker_model)


async def rerank(query: str, chunks: list[RetrievedChunk], top_k: int, max_per_doc: int = 0) -> list[RetrievedChunk]:
    """Score each (query, chunk) pair with a cross-encoder and keep the best top_k.

    With `max_per_doc`, no document takes more than that many slots while others have
    candidates, so a question spanning two documents isn't crowded out by one of them.
    """
    if not chunks:
        return []
    # Include the heading path: a "Sick leave" section often never repeats the words "sick leave".
    texts = [" > ".join(c.headings) + "\n" + c.text if c.headings else c.text for c in chunks]
    scores = await asyncio.to_thread(lambda: list(_encoder().rerank(query, texts)))
    for chunk, score in zip(chunks, scores, strict=True):
        chunk.score = float(score)
    return diversify(sorted(chunks, key=lambda c: c.score, reverse=True), top_k, max_per_doc)


def diversify(ranked: list[RetrievedChunk], top_k: int, max_per_doc: int) -> list[RetrievedChunk]:
    """Top `top_k` of score-sorted chunks, at most `max_per_doc` per document while others remain."""
    if not max_per_doc:
        return ranked[:top_k]
    picked, overflow, per_doc = [], [], Counter()
    for c in ranked:
        if per_doc[c.document_id] < max_per_doc:
            picked.append(c)
            per_doc[c.document_id] += 1
        else:
            overflow.append(c)
    # Fill remaining slots in score order if diversity left them empty.
    return sorted(picked[:top_k] + overflow[: max(0, top_k - len(picked))], key=lambda c: c.score, reverse=True)
