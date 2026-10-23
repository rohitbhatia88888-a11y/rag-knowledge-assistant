"""Background ingest worker (arq on Redis). Run with: arq app.worker.WorkerSettings"""

import logging
import uuid

from arq.connections import RedisSettings

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.ingest.pipeline import ingest_document
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.services import get_store

logging.basicConfig(level=logging.INFO)


async def ingest_job(ctx: dict, document_id: str) -> dict:
    return await ingest_document(uuid.UUID(document_id), SessionLocal, get_store())


async def startup(ctx: dict) -> None:
    await init_db()
    # Load models once up front so the first job doesn't pay for it.
    dense = get_dense_embedder()
    get_sparse_embedder()
    await get_store().ensure_collection(dense.dim)


class WorkerSettings:
    functions = [ingest_job]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 1800
