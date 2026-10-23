import asyncio

from app.auth import Principal
from app.config import get_settings
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.retrieval.rerank import rerank
from app.retrieval.store import RetrievedChunk, VectorStore


async def retrieve(store: VectorStore, principal: Principal, query: str) -> list[RetrievedChunk]:
    """Hybrid search restricted to the principal's ACL, then cross-encoder re-ranking.

    If even the best chunk scores below `min_rerank_score`, returns [] so the caller answers
    "I don't know" without calling the LLM. Otherwise returns the top `final_k`: lower-ranked
    chunks stay, because multi-part questions need passages that each match only half the query.
    """
    s = get_settings()
    dense, sparse = await asyncio.gather(
        get_dense_embedder().embed_query(query),
        get_sparse_embedder().embed_query(query),
    )
    candidates = await store.hybrid_search(
        principal=principal, dense=dense, sparse=sparse, candidate_k=s.candidate_k, limit=s.fused_k
    )
    ranked = await rerank(query, candidates, s.final_k)
    if not ranked or ranked[0].score < s.min_rerank_score:
        return []
    return ranked
