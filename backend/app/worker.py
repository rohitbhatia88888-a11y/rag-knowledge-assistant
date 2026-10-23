"""Background worker (arq on Redis): document ingestion and connector syncs.

Run with: arq app.worker.WorkerSettings
"""

import logging
import uuid

from arq import cron
from arq.worker import func
from arq.connections import RedisSettings

from app.config import get_settings
from app.connectors.runner import enabled_connector_ids, run_sync, sync_job_id
from app.db import SessionLocal, init_db
from app.ingest.pipeline import ingest_document
from app.retrieval.embeddings import get_dense_embedder, get_sparse_embedder
from app.services import get_store

logging.basicConfig(level=logging.INFO)


async def ingest_job(ctx: dict, document_id: str) -> dict:
    return await ingest_document(uuid.UUID(document_id), SessionLocal, get_store())


async def sync_connector_job(ctx: dict, connector_id: str, full: bool = False) -> dict:
    return await run_sync(uuid.UUID(connector_id), SessionLocal, get_store(), full=full)


async def schedule_syncs(ctx: dict) -> None:
    """Cron: queue an incremental sync for every enabled connector (it upgrades itself to full when due)."""
    for cid in await enabled_connector_ids(SessionLocal):
        await ctx["redis"].enqueue_job("sync_connector_job", str(cid), _job_id=sync_job_id(cid))


async def startup(ctx: dict) -> None:
    await init_db()
    # Load models once up front so the first job doesn't pay for it.
    dense = get_dense_embedder()
    get_sparse_embedder()
    await get_store().ensure_collection(dense.dim)


_every = get_settings().connector_sync_minutes


class WorkerSettings:
    # keep_result=0 frees the job id as soon as a sync finishes, so the next one can be queued.
    functions = [ingest_job, func(sync_connector_job, keep_result=0)]
    cron_jobs = [cron(schedule_syncs, minute=set(range(0, 60, _every)), run_at_startup=False)]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 3600
