"""Permission-aware retrieval against an in-memory Qdrant with the real embedding and re-ranking models."""

import uuid
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient

from app.auth import Principal
from app.db import Document
from app.ingest.pipeline import index_file
from app.retrieval.embeddings import get_dense_embedder
from app.retrieval.pipeline import retrieve
from app.retrieval.store import VectorStore

HR_DOC = """# Compensation bands

## Engineering salaries

Senior engineers in band L5 earn between 180,000 and 220,000 dollars per year.
"""

PUBLIC_DOC = """# Office handbook

## Wi-Fi

The guest Wi-Fi network is called AcmeGuest and the password rotates every Monday.
"""

ALICE_HR = Principal("alice", "acme", ("hr",))
BOB = Principal("bob", "acme", ())
EVE_OTHER_TENANT = Principal("eve", "globex", ("hr",))


def make_doc(tmp_path: Path, text: str, acl: list[str], tenant: str = "acme") -> Document:
    doc_id = uuid.uuid4()
    path = tmp_path / f"{doc_id}.md"
    path.write_text(text)
    return Document(
        id=doc_id, tenant_id=tenant, title=path.stem, source="upload", source_uri=None,
        mime_type="text/markdown", storage_path=str(path), acl=acl, created_by="alice",
    )


@pytest.fixture
async def store(tmp_path):
    s = VectorStore(AsyncQdrantClient(location=":memory:"), "chunks")
    await s.ensure_collection(get_dense_embedder().dim)
    return s


async def test_acl_filters_inside_search(store, tmp_path):
    hr = make_doc(tmp_path, HR_DOC, ["group:hr", "user:alice"])
    public = make_doc(tmp_path, PUBLIC_DOC, ["group:everyone"])
    await index_file(hr, store)
    await index_file(public, store)

    q = "How much do L5 senior engineers earn?"
    alice = await retrieve(store, ALICE_HR, q)
    assert alice and alice[0].document_id == str(hr.id)

    bob = await retrieve(store, BOB, q)
    assert all(c.document_id != str(hr.id) for c in bob)

    # same group name in a different tenant grants nothing
    assert await retrieve(store, EVE_OTHER_TENANT, q) == []

    wifi = await retrieve(store, BOB, "what is the guest wifi network name")
    assert wifi and wifi[0].document_id == str(public.id)


async def test_acl_change_applies_without_reembedding(store, tmp_path):
    hr = make_doc(tmp_path, HR_DOC, ["group:hr"])
    await index_file(hr, store)
    q = "L5 engineer salary band"
    assert await retrieve(store, BOB, q) == []

    await store.set_document_payload(str(hr.id), {"acl": ["group:hr", "user:bob"]})
    assert (await retrieve(store, BOB, q))[0].document_id == str(hr.id)


async def test_reindex_only_embeds_changed_chunks(store, tmp_path):
    doc = make_doc(tmp_path, HR_DOC + "\n## Bonuses\n\nBonus target is 10 percent.\n", ["group:hr"])
    first = await index_file(doc, store)
    assert first["embedded"] == first["chunks"] == 2

    Path(doc.storage_path).write_text(HR_DOC + "\n## Bonuses\n\nBonus target is 15 percent.\n")
    second = await index_file(doc, store)
    assert second == {"pages": 1, "chunks": 2, "embedded": 1, "removed": 1}
    hits = await retrieve(store, ALICE_HR, "bonus target percentage")
    assert "15 percent" in hits[0].text
