import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run_with(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_cli() -> None:
    """`alembic upgrade head` / `alembic revision --autogenerate` from the command line."""
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.run_sync(run_with)
        await conn.commit()
    await engine.dispose()


# app.migrate passes an open connection; the CLI does not.
if (connection := context.config.attributes.get("connection")) is not None:
    run_with(connection)
else:
    asyncio.run(run_cli())
