import asyncio

from app.auth import Principal
from app.config import get_settings
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.retrieval.rerank import rerank
from app.retrieval.store import RetrievedChunk, VectorStore


async def retrieve(store: VectorStore, principal: Principal, query: str) -> list[RetrievedChunk]:
    """Hybrid search restricted to the principal's ACL, then cross-encoder re-ranking.

    Chunks scoring below `min_rerank_score` are dropped, so an empty result means
    "nothing relevant you are allowed to see" and the caller should answer I don't know.
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
    return [c for c in ranked if c.score >= s.min_rerank_score]
