"""Run one connector sync and record the outcome on the connector row."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.connectors.gdrive.client import DriveClient, GoogleDriveClient
from app.connectors.gdrive.sync import DriveSync
from app.db import Connector
from app.retrieval.store import VectorStore

log = logging.getLogger(__name__)


def sync_job_id(connector_id) -> str:
    """One queued/running sync per connector: arq won't enqueue a job whose id already exists."""
    return f"sync:{connector_id}"


def make_drive_client(connector: Connector) -> DriveClient:
    path = get_settings().google_service_account_file
    if not path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is not set")
    return GoogleDriveClient(path, subject=connector.config.get("subject"))


async def run_sync(
    connector_id: uuid.UUID,
    sessions: async_sessionmaker,
    store: VectorStore,
    full: bool = False,
    client: DriveClient | None = None,
) -> dict:
    async with sessions() as db:
        connector = await db.get(Connector, connector_id)
        if connector is None or not connector.enabled:
            return {"status": "skipped"}
        connector.status = "syncing"
        await db.commit()

    s = get_settings()
    due_full = connector.last_full_sync_at is None or (
        datetime.now(timezone.utc) - _aware(connector.last_full_sync_at)
        > timedelta(hours=s.connector_full_sync_hours)
    )
    full = full or due_full
    started = datetime.now(timezone.utc)
    own_client = client is None
    sync = None
    try:
        client = client or make_drive_client(connector)
        sync = DriveSync(connector, client, sessions, store, Path(s.upload_dir))
        state = await sync.run(full=full)
        error = None
    except Exception as e:
        log.exception("sync of connector %s failed", connector_id)
        state, error = None, f"{type(e).__name__}: {e}"[:2000]
    finally:
        if own_client and client is not None and hasattr(client, "aclose"):
            await client.aclose()

    stats = sync.stats_dict() if sync else {}
    async with sessions() as db:
        connector = await db.get(Connector, connector_id)
        connector.last_sync_at = started
        connector.stats = {**stats, "full": full, "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1)}
        if error:
            connector.status, connector.last_error = "failed", error
        else:
            connector.status, connector.last_error, connector.state = "ok", None, state
            if full:
                connector.last_full_sync_at = started
        await db.commit()
    log.info("connector %s synced (full=%s): %s", connector_id, full, stats)
    return {"status": connector.status, **connector.stats}


async def enabled_connector_ids(sessions: async_sessionmaker) -> list[uuid.UUID]:
    async with sessions() as db:
        return list(await db.scalars(select(Connector.id).where(Connector.enabled.is_(True))))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
