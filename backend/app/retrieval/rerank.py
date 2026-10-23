import asyncio
from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import get_settings
from app.retrieval.store import RetrievedChunk


@lru_cache
def _encoder() -> TextCrossEncoder:
    return TextCrossEncoder(get_settings().reranker_model)


async def rerank(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Score each (query, chunk) pair with a cross-encoder and keep the best top_k."""
    if not chunks:
        return []
    scores = await asyncio.to_thread(lambda: list(_encoder().rerank(query, [c.text for c in chunks])))
    for chunk, score in zip(chunks, scores, strict=True):
        chunk.score = float(score)
    return sorted(chunks, key=lambda c: c.score, reverse=True)[:top_k]
