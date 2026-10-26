import hashlib
import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.auth import Principal, current_principal
from app.config import get_settings
from app.db import AsyncSession, Document, get_db
from app.services import get_store

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PRINCIPAL_RE = re.compile(r"^(user|group|domain):[\w.@+-]+$")


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    source: str
    mime_type: str
    acl: list[str]
    status: str
    error: str | None
    page_count: int | None
    chunk_count: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AclUpdate(BaseModel):
    acl: list[str]

    @field_validator("acl")
    @classmethod
    def check(cls, v: list[str]) -> list[str]:
        return _validate_acl(v)


def _validate_acl(entries: list[str]) -> list[str]:
    cleaned = sorted({e.strip() for e in entries if e.strip()})
    bad = [e for e in cleaned if not PRINCIPAL_RE.match(e)]
    if bad:
        raise ValueError(f"ACL entries must look like user:<id>, group:<name> or domain:<domain>, got {bad}")
    if not cleaned:
        raise ValueError("ACL cannot be empty")
    return cleaned


def can_read(doc: Document, principal: Principal) -> bool:
    return doc.tenant_id == principal.tenant_id and bool(set(doc.acl) & set(principal.principals))


async def _get_readable(db: AsyncSession, document_id: uuid.UUID, principal: Principal) -> Document:
    doc = await db.get(Document, document_id)
    # 404 rather than 403 so callers can't probe for documents they can't see.
    if doc is None or not can_read(doc, principal):
        raise HTTPException(404, "Document not found")
    return doc


async def _get_owned(db: AsyncSession, document_id: uuid.UUID, principal: Principal) -> Document:
    doc = await _get_readable(db, document_id, principal)
    if doc.created_by != principal.user_id:
        raise HTTPException(403, "Only the uploader can change this document")
    return doc


@router.post("", response_model=DocumentOut, status_code=201)
async def upload(
    request: Request,
    file: UploadFile,
    acl: str | None = Form(None, description="Comma-separated principals, e.g. 'group:hr,user:bob'"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
):
    if get_settings().demo_mode:
        raise HTTPException(403, "Uploads are disabled in the public demo")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"Unsupported file type {suffix!r}; allowed: {sorted(ALLOWED_SUFFIXES)}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (50 MB max)")
    try:
        acl_list = _validate_acl(acl.split(",") if acl else [f"user:{principal.user_id}"])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    # The uploader always keeps access to their own document.
    acl_list = sorted(set(acl_list) | {f"user:{principal.user_id}"})

    doc_id = uuid.uuid4()
    dest = Path(get_settings().upload_dir) / principal.tenant_id / f"{doc_id}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    doc = Document(
        id=doc_id,
        tenant_id=principal.tenant_id,
        title=Path(file.filename or "untitled").stem,
        mime_type=file.content_type or mimetypes.guess_type(dest.name)[0] or "application/octet-stream",
        storage_path=str(dest),
        content_hash=hashlib.sha256(data).hexdigest(),
        acl=acl_list,
        created_by=principal.user_id,
    )
    db.add(doc)
    await db.commit()
    await request.app.state.arq.enqueue_job("ingest_job", str(doc_id))
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Document).where(Document.tenant_id == principal.tenant_id).order_by(Document.created_at.desc())
    )
    return [d for d in rows if can_read(d, principal)]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
):
    return await _get_readable(db, document_id, principal)


@router.get("/{document_id}/file")
async def get_file(
    document_id: uuid.UUID, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
):
    doc = await _get_readable(db, document_id, principal)
    return FileResponse(doc.storage_path, media_type=doc.mime_type, content_disposition_type="inline")


@router.patch("/{document_id}/acl", response_model=DocumentOut)
async def update_acl(
    document_id: uuid.UUID,
    body: AclUpdate,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Permission changes only touch payloads; nothing is re-embedded."""
    doc = await _get_owned(db, document_id, principal)
    doc.acl = sorted(set(body.acl) | {f"user:{doc.created_by}"})
    await get_store().set_document_payload(str(doc.id), {"acl": doc.acl})
    await db.commit()
    await db.refresh(doc)
    return doc


@router.post("/{document_id}/reindex", response_model=DocumentOut)
async def reindex(
    request: Request,
    document_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_owned(db, document_id, principal)
    doc.status = "queued"
    await db.commit()
    await request.app.state.arq.enqueue_job("ingest_job", str(doc.id))
    return doc


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
):
    doc = await _get_owned(db, document_id, principal)
    await get_store().delete_document(str(doc.id))
    if doc.storage_path:
        Path(doc.storage_path).unlink(missing_ok=True)
    await db.delete(doc)
    await db.commit()
