import asyncio

from app.auth import Principal
from app.config import get_settings
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.retrieval.rerank import rerank
from app.retrieval.store import RetrievedChunk, VectorStore
from app.tracing import observation


async def rank(store: VectorStore, principal: Principal, query: str) -> list[RetrievedChunk]:
    """Hybrid search restricted to the principal's ACL, then cross-encoder re-ranking to the top `final_k`."""
    s = get_settings()
    with observation("retrieve", as_type="retriever", input={"query": query}) as obs:
        dense, sparse = await asyncio.gather(
            get_dense_embedder().embed_query(query),
            get_sparse_embedder().embed_query(query),
        )
        candidates = await store.hybrid_search(
            principal=principal, dense=dense, sparse=sparse, candidate_k=s.candidate_k, limit=s.fused_k
        )
        ranked = await rerank(query, candidates, s.final_k, s.max_chunks_per_doc)
        obs.update(
            output=[
                {"chunk_id": c.id, "document": c.document_title, "page": c.page_start, "score": round(c.score, 3)}
                for c in ranked
            ],
            metadata={"candidates": len(candidates)},
        )
        return ranked


def passes_gate(ranked: list[RetrievedChunk]) -> bool:
    """Answer only if the best chunk clears `min_rerank_score`.

    Lower-ranked chunks are kept when it does, because multi-part questions need
    passages that each match only half the query.
    """
    return bool(ranked) and ranked[0].score >= get_settings().min_rerank_score


async def retrieve(store: VectorStore, principal: Principal, query: str) -> list[RetrievedChunk]:
    """Ranked chunks to answer from, or [] meaning "nothing relevant you are allowed to see"."""
    ranked = await rank(store, principal, query)
    return ranked if passes_gate(ranked) else []
