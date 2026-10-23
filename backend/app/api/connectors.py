"""Admin API for connectors. Only members of the `admins` group can use it."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth import Principal, require_admin
from app.db import AsyncSession, Connector, Document, get_db
from app.services import get_store
from app.connectors.runner import sync_job_id

router = APIRouter(prefix="/connectors", tags=["connectors"])


class GDriveConfig(BaseModel):
    folder_ids: list[str] = Field(min_length=1, description="Drive folder IDs to index, including subfolders")
    subject: str | None = Field(None, description="Workspace user to impersonate (domain-wide delegation)")


class ConnectorIn(BaseModel):
    kind: Literal["gdrive"] = "gdrive"
    name: str = Field(min_length=1, max_length=256)
    config: GDriveConfig


class ConnectorOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    config: dict
    enabled: bool
    status: str
    last_sync_at: datetime | None
    last_full_sync_at: datetime | None
    last_error: str | None
    stats: dict | None
    document_count: int = 0
    model_config = {"from_attributes": True}


async def _get(db: AsyncSession, connector_id: uuid.UUID, principal: Principal) -> Connector:
    c = await db.get(Connector, connector_id)
    if c is None or c.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Connector not found")
    return c


async def _out(db: AsyncSession, c: Connector) -> ConnectorOut:
    count = await db.scalar(select(func.count()).select_from(Document).where(Document.connector_id == c.id))
    return ConnectorOut.model_validate(c).model_copy(update={"document_count": count or 0})


@router.post("", response_model=ConnectorOut, status_code=201)
async def create(
    body: ConnectorIn, request: Request, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    c = Connector(
        tenant_id=principal.tenant_id,
        kind=body.kind,
        name=body.name,
        config=body.config.model_dump(exclude_none=True),
        state={},
        enabled=True,
        status="new",
        created_by=principal.user_id,
    )
    db.add(c)
    await db.commit()
    await request.app.state.arq.enqueue_job("sync_connector_job", str(c.id), True, _job_id=sync_job_id(c.id))
    return await _out(db, c)


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Connector).where(Connector.tenant_id == principal.tenant_id).order_by(Connector.created_at))
    return [await _out(db, c) for c in rows]


@router.post("/{connector_id}/sync", response_model=ConnectorOut)
async def trigger_sync(
    connector_id: uuid.UUID,
    request: Request,
    full: bool = False,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    c = await _get(db, connector_id, principal)
    job = await request.app.state.arq.enqueue_job("sync_connector_job", str(c.id), full, _job_id=sync_job_id(c.id))
    if job is None:
        raise HTTPException(409, "A sync for this connector is already queued or running")
    return await _out(db, c)


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def set_enabled(
    connector_id: uuid.UUID, enabled: bool, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    c = await _get(db, connector_id, principal)
    c.enabled = enabled
    await db.commit()
    return await _out(db, c)


@router.delete("/{connector_id}", status_code=204)
async def delete(connector_id: uuid.UUID, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Removes the connector and everything it indexed."""
    c = await _get(db, connector_id, principal)
    docs = list(await db.scalars(select(Document).where(Document.connector_id == c.id)))
    store = get_store()
    for d in docs:
        await store.delete_document(str(d.id))
        if d.storage_path:
            Path(d.storage_path).unlink(missing_ok=True)
        await db.delete(d)
    await db.delete(c)
    await db.commit()
