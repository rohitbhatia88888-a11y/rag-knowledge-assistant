"""Ingest one document: parse, chunk, and re-embed only the chunks whose content changed."""

import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import Document
from app.ingest.chunk import Chunk, chunk_blocks
from app.ingest.parse import parse_file
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.retrieval.store import VectorStore, point_id

log = logging.getLogger(__name__)


async def ingest_document(document_id: uuid.UUID, sessions: async_sessionmaker, store: VectorStore) -> dict:
    async with sessions() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            return {"status": "missing"}
        doc.status, doc.error = "processing", None
        await db.commit()

        try:
            stats = await index_file(doc, store)
            doc.status = "ready"
            doc.page_count = stats["pages"]
            doc.chunk_count = stats["chunks"]
        except Exception as e:
            log.exception("ingest failed for %s", document_id)
            doc.status, doc.error = "failed", str(e)[:2000]
            stats = {"error": str(e)}
        await db.commit()
        return stats


async def index_file(doc: Document, store: VectorStore) -> dict:
    s = get_settings()
    parsed = parse_file(Path(doc.storage_path), doc.mime_type)
    chunks = _dedupe(chunk_blocks(parsed.blocks, s.chunk_target_chars, s.chunk_overlap_chars))
    if not chunks:
        raise ValueError("No extractable text (scanned PDFs need OCR, which is not enabled yet)")

    doc_id = str(doc.id)
    wanted = {point_id(doc_id, c.content_hash): c for c in chunks}
    existing = await store.existing_hashes(doc_id)
    to_embed = [c for pid, c in wanted.items() if pid not in existing]
    stale = [pid for pid in existing if pid not in wanted]

    base_payload = {
        "tenant_id": doc.tenant_id,
        "document_id": doc_id,
        "document_title": doc.title,
        "source": doc.source,
        "source_uri": doc.source_uri,
        "acl": doc.acl,
    }
    if to_embed:
        texts = [c.embed_text for c in to_embed]
        dense = await get_dense_embedder().embed_documents(texts)
        sparse = await get_sparse_embedder().embed_documents(texts)
        await store.upsert_chunks(chunks=to_embed, dense=dense, sparse=sparse, base_payload=base_payload)
    await store.delete_points(stale)
    # Unchanged chunks keep their vectors but must pick up title/ACL changes.
    await store.set_document_payload(doc_id, {k: base_payload[k] for k in ("document_title", "acl", "source_uri")})

    log.info("doc %s: %d chunks, %d embedded, %d removed", doc_id, len(chunks), len(to_embed), len(stale))
    return {"pages": parsed.page_count, "chunks": len(chunks), "embedded": len(to_embed), "removed": len(stale)}


def _dedupe(chunks: list[Chunk]) -> list[Chunk]:
    seen: set[str] = set()
    out = []
    for c in chunks:
        if c.content_hash not in seen:
            seen.add(c.content_hash)
            out.append(c)
    return out
