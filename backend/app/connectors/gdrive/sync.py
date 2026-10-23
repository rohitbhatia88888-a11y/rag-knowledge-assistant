"""Google Drive sync: a full crawl to start, then incremental syncs from the Drive changes feed.

Scope is a set of root folders and everything below them. The connector state keeps the changes
page token and the folder tree (`{folder_id: parent_id}`) so a change can be placed in or out of scope
without asking Drive for ancestors.

Per file, content and permissions are handled separately:
- content changed (md5 / modifiedTime differs): download, re-index; unchanged chunks keep their vectors
- only sharing, title or location changed: update chunk payloads, nothing is re-embedded
- deleted, trashed, unshared, moved out of scope, or an unsupported type: removed from the index
"""

import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.gdrive.acl import permissions_to_acl
from app.connectors.gdrive.client import GOOGLE_DOC_MIME, Change, DriveClient, DriveFile
from app.db import Connector, Document
from app.ingest.pipeline import index_file
from app.retrieval.store import VectorStore

log = logging.getLogger(__name__)

# Drive MIME type -> (stored MIME type, file suffix). Google Docs are exported as Markdown.
SUPPORTED = {
    "application/pdf": ("application/pdf", ".pdf"),
    "text/markdown": ("text/markdown", ".md"),
    "text/x-markdown": ("text/markdown", ".md"),
    "text/plain": ("text/plain", ".txt"),
    GOOGLE_DOC_MIME: ("text/markdown", ".md"),
}
MAX_BYTES = 50 * 1024 * 1024


@dataclass
class SyncStats:
    added: int = 0
    updated: int = 0
    metadata_only: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    changes_seen: int = 0


class DriveSync:
    def __init__(
        self,
        connector: Connector,
        client: DriveClient,
        sessions: async_sessionmaker,
        store: VectorStore,
        upload_dir: Path,
    ) -> None:
        self.connector = connector
        self.client = client
        self.sessions = sessions
        self.store = store
        self.upload_dir = upload_dir
        self.roots: list[str] = list(connector.config.get("folder_ids", []))
        self.stats = SyncStats()

    async def run(self, full: bool = False) -> dict:
        """Sync and return the new connector state. The caller persists it."""
        if not self.roots:
            raise ValueError("Connector has no folder_ids configured")
        state = self.connector.state or {}
        if full or not state.get("page_token"):
            return await self.full_sync()
        return await self.incremental_sync(state["page_token"], dict(state.get("folders", {})))

    # ------------------------------------------------------------------ full crawl

    async def full_sync(self) -> dict:
        # Take the token first so anything that changes during the crawl is replayed next time.
        token = await self.client.start_page_token()
        folders: dict[str, str | None] = {root: None for root in self.roots}
        seen: set[str] = set()
        for root in self.roots:
            for f in await self._crawl(root, folders):
                if f.id not in seen:  # a file can live in several folders
                    seen.add(f.id)
                    await self._upsert(f)
        await self._delete_where(lambda d: d.external_id not in seen)
        return {"page_token": token, "folders": folders}

    async def _crawl(self, folder_id: str, folders: dict[str, str | None]) -> list[DriveFile]:
        """All non-folder files below folder_id, registering subfolders in `folders` as it goes."""
        files, queue = [], [folder_id]
        while queue:
            current = queue.pop()
            for item in await self.client.list_children(current):
                if item.is_folder:
                    if item.id not in folders:
                        folders[item.id] = current
                        queue.append(item.id)
                else:
                    files.append(item)
        return files

    # ------------------------------------------------------------------ incremental

    async def incremental_sync(self, token: str, folders: dict[str, str | None]) -> dict:
        while True:
            changes, next_token, new_start = await self.client.list_changes(token)
            for change in changes:
                self.stats.changes_seen += 1
                await self._apply(change, folders)
            if next_token:
                token = next_token
                continue
            return {"page_token": new_start or token, "folders": folders}

    async def _apply(self, change: Change, folders: dict[str, str | None]) -> None:
        f = change.file
        gone = change.removed or f is None or f.trashed
        is_folder = change.file_id in folders or (f is not None and f.is_folder)

        if is_folder:
            stays = (
                not gone
                and f is not None
                and (f.id in self.roots or any(p in folders for p in f.parents))
            )
            if stays and f.id not in folders:
                # A folder moved into scope (or a root restored from trash) brings its files along
                # without a change event for each, so crawl it.
                folders[f.id] = None if f.id in self.roots else next(p for p in f.parents if p in folders)
                for child in await self._crawl(f.id, folders):
                    await self._upsert(child)
            elif not stays and change.file_id in folders:
                for fid in self._subtree(change.file_id, folders):
                    folders.pop(fid, None)
                # Files under it are now out of scope unless they also live somewhere still in scope.
                await self._delete_where(lambda d: not any(p in folders for p in (d.external_parents or [])))
            elif stays and f is not None:
                folders[f.id] = None if f.id in self.roots else next(p for p in f.parents if p in folders)
            return

        if gone or not any(p in folders for p in f.parents):
            await self._delete_external(change.file_id)
            return
        await self._upsert(f)

    @staticmethod
    def _subtree(folder_id: str, folders: dict[str, str | None]) -> set[str]:
        out, frontier = {folder_id}, {folder_id}
        while frontier:
            frontier = {fid for fid, parent in folders.items() if parent in frontier and fid not in out}
            out |= frontier
        return out

    # ------------------------------------------------------------------ per file

    async def _upsert(self, f: DriveFile) -> None:
        async with self.sessions() as db:
            doc = await db.scalar(
                select(Document).where(
                    Document.connector_id == self.connector.id, Document.external_id == f.id
                )
            )
            if f.mime_type not in SUPPORTED or (f.size or 0) > MAX_BYTES:
                if doc is not None:
                    await self._delete(db, doc)
                    await db.commit()
                self.stats.skipped += 1
                return

            try:
                acl = permissions_to_acl(await self.client.list_permissions(f.id))
            except Exception as e:
                log.warning("permissions for %s (%s) failed: %s", f.name, f.id, e)
                self.stats.failed += 1
                return

            stored_mime, suffix = SUPPORTED[f.mime_type]
            title = f.name[: -len(suffix)] if f.name.lower().endswith(suffix) else f.name
            is_new = doc is None
            if is_new:
                doc = Document(
                    id=uuid.uuid4(),
                    tenant_id=self.connector.tenant_id,
                    source="gdrive",
                    created_by=f"connector:{self.connector.id}",
                    connector_id=self.connector.id,
                    external_id=f.id,
                    mime_type=stored_mime,
                    status="processing",
                )
                db.add(doc)

            content_changed = is_new or doc.external_version != f.version or doc.status != "ready"
            metadata_changed = (doc.acl, doc.title, doc.source_uri) != (acl, title, f.web_view_link)
            doc.acl, doc.title, doc.source_uri = acl, title, f.web_view_link
            doc.external_parents = f.parents

            if content_changed:
                await self._index(doc, f, stored_mime, suffix, is_new)
            elif metadata_changed:
                await self.store.set_document_payload(
                    str(doc.id), {"acl": acl, "document_title": title, "source_uri": f.web_view_link}
                )
                self.stats.metadata_only += 1
            else:
                self.stats.unchanged += 1
            await db.commit()

    async def _index(self, doc: Document, f: DriveFile, stored_mime: str, suffix: str, is_new: bool) -> None:
        try:
            data = await self.client.download(f)
            path = self.upload_dir / doc.tenant_id / f"{doc.id}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            doc.storage_path, doc.mime_type = str(path), stored_mime
            result = await index_file(doc, self.store)
        except Exception as e:
            log.warning("indexing %s (%s) failed: %s", f.name, f.id, e)
            # external_version stays stale, so the next sync retries this file.
            doc.status, doc.error = "failed", str(e)[:2000]
            self.stats.failed += 1
            return
        doc.status, doc.error = "ready", None
        doc.page_count, doc.chunk_count = result["pages"], result["chunks"]
        doc.external_version = f.version
        if is_new:
            self.stats.added += 1
        else:
            self.stats.updated += 1

    async def _delete_external(self, file_id: str) -> None:
        async with self.sessions() as db:
            doc = await db.scalar(
                select(Document).where(
                    Document.connector_id == self.connector.id, Document.external_id == file_id
                )
            )
            if doc is not None:
                await self._delete(db, doc)
                await db.commit()

    async def _delete_where(self, predicate) -> None:
        """Delete this connector's documents matching a Python predicate (used for scope-wide pruning)."""
        async with self.sessions() as db:
            docs = await db.scalars(select(Document).where(Document.connector_id == self.connector.id))
            for doc in [d for d in docs if predicate(d)]:
                await self._delete(db, doc)
            await db.commit()

    async def _delete(self, db, doc: Document) -> None:
        await self.store.delete_document(str(doc.id))
        if doc.storage_path:
            Path(doc.storage_path).unlink(missing_ok=True)
        await db.delete(doc)
        self.stats.deleted += 1

    def stats_dict(self) -> dict:
        return asdict(self.stats)
