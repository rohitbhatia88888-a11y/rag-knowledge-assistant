"""Qdrant vector store: one collection, dense + BM25 vectors per chunk, ACL and tenant in the payload.

Permission filtering happens inside the search (both prefetch branches), never after it,
so a user's top-k is drawn only from chunks they can read.
"""

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.auth import Principal
from app.ingest.chunk import Chunk

DENSE = "dense"
SPARSE = "bm25"
POINT_NAMESPACE = uuid.UUID("6f1c2b8e-7a51-4c7e-9d0b-3f2a9c4e5b10")


@dataclass
class RetrievedChunk:
    id: str
    text: str
    document_id: str
    document_title: str
    headings: list[str]
    page_start: int
    page_end: int
    source_uri: str | None
    score: float  # fusion score, replaced by the re-ranker score later

    @classmethod
    def from_point(cls, point: models.ScoredPoint) -> "RetrievedChunk":
        p = point.payload or {}
        return cls(
            id=str(point.id),
            text=p["text"],
            document_id=p["document_id"],
            document_title=p["document_title"],
            headings=p.get("headings", []),
            page_start=p["page_start"],
            page_end=p["page_end"],
            source_uri=p.get("source_uri"),
            score=point.score,
        )


def point_id(document_id: str, content_hash: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"{document_id}:{content_hash}"))


def permission_filter(principal: Principal) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=principal.tenant_id)),
            models.FieldCondition(key="acl", match=models.MatchAny(any=principal.principals)),
        ]
    )


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self.client = client
        self.collection = collection

    async def ensure_collection(self, dense_dim: int) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={DENSE: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        # is_tenant co-locates each tenant's points on disk, which keeps per-tenant filtered search fast.
        await self.client.create_payload_index(
            self.collection,
            "tenant_id",
            models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        for field in ("acl", "document_id"):
            await self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)

    async def existing_hashes(self, document_id: str) -> dict[str, str]:
        """point id -> content hash for every chunk currently indexed for a document."""
        out: dict[str, str] = {}
        offset = None
        while True:
            points, offset = await self.client.scroll(
                self.collection,
                scroll_filter=_doc_filter(document_id),
                with_payload=["content_hash"],
                limit=512,
                offset=offset,
            )
            out.update({str(p.id): p.payload["content_hash"] for p in points})
            if offset is None:
                return out

    async def upsert_chunks(
        self,
        *,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[models.SparseVector],
        base_payload: dict,
    ) -> None:
        points = [
            models.PointStruct(
                id=point_id(base_payload["document_id"], c.content_hash),
                vector={DENSE: d, SPARSE: s},
                payload={
                    **base_payload,
                    "text": c.text,
                    "headings": c.headings,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "is_table": c.is_table,
                    "content_hash": c.content_hash,
                },
            )
            for c, d, s in zip(chunks, dense, sparse, strict=True)
        ]
        for i in range(0, len(points), 128):
            await self.client.upsert(self.collection, points[i : i + 128])

    async def delete_points(self, ids: list[str]) -> None:
        if ids:
            await self.client.delete(self.collection, models.PointIdsList(points=ids))

    async def delete_document(self, document_id: str) -> None:
        await self.client.delete(self.collection, models.FilterSelector(filter=_doc_filter(document_id)))

    async def set_document_payload(self, document_id: str, payload: dict) -> None:
        """Change metadata such as the ACL on every chunk of a document without re-embedding."""
        await self.client.set_payload(self.collection, payload=payload, points=_doc_filter(document_id))

    async def hybrid_search(
        self,
        *,
        principal: Principal,
        dense: list[float],
        sparse: models.SparseVector,
        candidate_k: int,
        limit: int,
    ) -> list[RetrievedChunk]:
        flt = permission_filter(principal)
        result = await self.client.query_points(
            self.collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=candidate_k, filter=flt),
                models.Prefetch(query=sparse, using=SPARSE, limit=candidate_k, filter=flt),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        return [RetrievedChunk.from_point(p) for p in result.points]


def _doc_filter(document_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])
