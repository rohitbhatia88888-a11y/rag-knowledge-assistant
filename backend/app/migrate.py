"""Run Alembic migrations at startup. The API and the worker both call this, so it takes a lock."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

BACKEND = Path(__file__).resolve().parent.parent
INITIAL_REVISION = "0001"
LOCK_ID = 7263540  # arbitrary, shared by every process that migrates


def _upgrade(sync_conn) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["connection"] = sync_conn
    insp = inspect(sync_conn)
    # Databases created before migrations existed (Phases 1-2 used create_all) match 0001 exactly.
    if insp.has_table("documents") and not insp.has_table("alembic_version"):
        command.stamp(cfg, INITIAL_REVISION)
    command.upgrade(cfg, "head")


async def upgrade_to_head() -> None:
    from app.db import engine

    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text(f"SELECT pg_advisory_xact_lock({LOCK_ID})"))
        await conn.run_sync(_upgrade)
