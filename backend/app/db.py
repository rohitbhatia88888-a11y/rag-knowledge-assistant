import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload | gdrive | notion | confluence
    source_uri: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(128))
    storage_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Principals allowed to read, e.g. ["user:alice", "group:hr", "group:everyone"].
    acl: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | processing | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set for documents owned by a connector (e.g. a Google Drive file); uploads leave them null.
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    external_id: Mapped[str | None] = mapped_column(String(256))
    # Source-side version (md5 or modifiedTime); unchanged means skip download and re-index.
    external_version: Mapped[str | None] = mapped_column(String(128))
    external_parents: Mapped[list | None] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("connector_id", "external_id", name="uq_documents_connector_external"),)


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # gdrive
    name: Mapped[str] = mapped_column(String(256))
    # Non-secret settings, e.g. {"folder_ids": [...], "subject": "admin@corp.com"}. Credentials stay in env/files.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Sync cursor and folder tree, e.g. {"page_token": "123", "folders": {"id": "parent_id"}}.
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | syncing | ok | failed
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    search_query: Mapped[str | None] = mapped_column(Text)
    # Chunks shown to the LLM (id, document, page, score) and which ones it cited.
    sources: Mapped[list | None] = mapped_column(JSON)
    cited_chunk_ids: Mapped[list | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(32))  # Langfuse trace, when tracing is on
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(128))
    rating: Mapped[int] = mapped_column(SmallInteger)  # 1 or -1
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Bring the schema to the latest Alembic revision (see backend/migrations)."""
    from app.migrate import upgrade_to_head

    await upgrade_to_head()


__all__ = [
    "AsyncSession", "Base", "ChatSession", "Connector", "Document", "Feedback", "Message",
    "SessionLocal", "get_db", "init_db",
]
