"""Public demo: the Halcyon Robotics corpus, the personas visitors can pick, and seeding.

The demo runs as a single container (see deploy/huggingface/): SQLite and an on-disk Qdrant,
both filled at image build time by `python -m app.demo seed`, so nothing is indexed at startup.
"""

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pymupdf
from sqlalchemy import func, select

from app.config import get_settings
from app.db import Document

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = BACKEND.parent / "eval" / "corpus"

# Persona id -> what the visitor sees. Groups come from eval/corpus/manifest.json.
PERSONAS = {
    "employee": {
        "name": "Employee",
        "about": "Sees company-wide policies only.",
        "questions": [
            "Can I expense my gym membership?",
            "What is the hotel cap in London?",
            "What is the salary range for a Senior Engineer?",
        ],
    },
    "engineer": {
        "name": "Engineer",
        "about": "Also sees the on-call runbook, deployment SOP and incident postmortems.",
        "questions": [
            "What caused the Fleet Console outage in March 2026?",
            "Can I deploy to production on a Friday?",
            "How much is primary on-call paid?",
        ],
    },
    "hr": {
        "name": "HR",
        "about": "Also sees the confidential engineering compensation bands.",
        "questions": [
            "What is the salary range for a Senior Engineer?",
            "How long must an engineer be at their level before promotion, and what raise does it bring?",
            "When is the annual code freeze?",
        ],
    },
    "support": {
        "name": "Support agent",
        "about": "Also sees the customer support SOP.",
        "questions": [
            "Can I refund a customer 2,500 dollars myself?",
            "What is the first response SLA for Enterprise customers?",
            "What caused the Fleet Console outage in March 2026?",
        ],
    },
}


def manifest(corpus_dir: Path) -> dict:
    return json.loads((corpus_dir / "manifest.json").read_text())


def persona_groups(persona: str, corpus_dir: Path | None = None) -> list[str]:
    return manifest(corpus_dir or corpus_path())["personas"][persona]


def corpus_path() -> Path:
    s = get_settings()
    return Path(s.demo_corpus_dir) if s.demo_corpus_dir else DEFAULT_CORPUS


def render_pdf(html_path: Path, out: Path) -> None:
    """Render an HTML source to PDF so tables and layout go through the real PDF parser."""
    css = (
        "body{font-family:sans-serif;font-size:11pt} h1{font-size:20pt} h2{font-size:15pt}"
        " table{border-collapse:collapse} td,th{border:1px solid black;padding:4px}"
    )
    story = pymupdf.Story(html=html_path.read_text(), user_css=css)
    writer = pymupdf.DocumentWriter(str(out))
    page = pymupdf.paper_rect("letter")
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(page + (54, 54, -54, -54))
        story.draw(device)
        writer.end_page()
    writer.close()


def build_corpus(workdir: Path, corpus_dir: Path | None = None) -> tuple[str, list[Document], dict[str, list[str]]]:
    """Materialise the corpus into workdir and return (tenant, unsaved Document rows, personas)."""
    corpus_dir = corpus_dir or corpus_path()
    m = manifest(corpus_dir)
    docs = []
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in m["documents"]:
        src = corpus_dir / entry["file"]
        if src.suffix == ".html":
            path, mime = workdir / f"{src.stem}.pdf", "application/pdf"
            render_pdf(src, path)
        else:
            path, mime = workdir / src.name, "text/markdown"
            shutil.copy(src, path)
        docs.append(
            Document(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"halcyon/{src.stem}"),
                tenant_id=m["tenant_id"],
                title=src.stem,
                source="upload",
                source_uri=None,
                mime_type=mime,
                storage_path=str(path),
                acl=entry["acl"],
                created_by="demo",
                status="processing",
            )
        )
    return m["tenant_id"], docs, m["personas"]


async def seed() -> None:
    """Create the schema and index the corpus once. Safe to run again."""
    from app.db import SessionLocal, init_db
    from app.ingest.pipeline import index_file
    from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
    from app.retrieval.rerank import _encoder
    from app.services import get_store

    await init_db()
    store = get_store()
    await store.ensure_collection(get_dense_embedder().dim)
    get_sparse_embedder()
    _encoder()  # download the re-ranker too, so the image starts without network fetches

    async with SessionLocal() as db:
        if await db.scalar(select(func.count()).select_from(Document)):
            print("demo data already seeded")
            return
        _, docs, _ = build_corpus(Path(get_settings().upload_dir) / "halcyon")
        for doc in docs:
            stats = await index_file(doc, store)
            doc.status, doc.page_count, doc.chunk_count = "ready", stats["pages"], stats["chunks"]
            db.add(doc)
            print(f"indexed {doc.title}: {stats['chunks']} chunks")
        await db.commit()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["seed"]:
        asyncio.run(seed())
    else:
        print("usage: python -m app.demo seed")
