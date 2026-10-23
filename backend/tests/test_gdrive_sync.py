"""Drive sync engine against an in-memory fake Drive (changes feed, permissions, folders),
SQLite for the documents table and an in-memory Qdrant with the real models."""

import uuid
from collections import Counter
from dataclasses import replace

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.connectors.gdrive.acl import permissions_to_acl
from app.connectors.gdrive.client import FOLDER_MIME, GOOGLE_DOC_MIME, Change, DriveFile
from app.connectors.gdrive.sync import DriveSync
from app.db import Base, Connector, Document
from app.retrieval.embeddings import get_dense_embedder
from app.retrieval.pipeline import rank
from app.retrieval.store import VectorStore

TENANT = "acme"
ALICE = Principal("alice@acme.com", TENANT)
BOB = Principal("bob@acme.com", TENANT)
CAROL_OTHER_DOMAIN = Principal("carol@contractor.io", TENANT)


class FakeDrive:
    """Just enough of Drive: files with parents, permissions, contents and an append-only changes log."""

    def __init__(self, page_size: int = 2) -> None:
        self.files: dict[str, DriveFile] = {}
        self.content: dict[str, bytes] = {}
        self.perms: dict[str, list[dict]] = {}
        self.log: list[Change] = []
        self.page_size = page_size
        self.downloads: Counter = Counter()
        self._rev = 0

    # --- DriveClient interface
    async def start_page_token(self) -> str:
        return str(len(self.log))

    async def list_children(self, folder_id: str) -> list[DriveFile]:
        return [f for f in self.files.values() if folder_id in f.parents and not f.trashed]

    async def list_changes(self, page_token: str):
        i = int(page_token)
        page = self.log[i : i + self.page_size]
        more = i + self.page_size < len(self.log)
        return page, (str(i + self.page_size) if more else None), (None if more else str(len(self.log)))

    async def list_permissions(self, file_id: str) -> list[dict]:
        return self.perms.get(file_id, [])

    async def download(self, file: DriveFile) -> bytes:
        self.downloads[file.id] += 1
        return self.content[file.id]

    # --- test helpers (each records a change like Drive does)
    def _emit(self, file_id: str, removed: bool = False) -> None:
        f = self.files.get(file_id)
        self.log.append(Change(file_id, removed, None if removed else replace(f)))

    def folder(self, fid: str, name: str, parent: str | None = None) -> None:
        self.files[fid] = DriveFile(fid, name, FOLDER_MIME, parents=[parent] if parent else [])
        self._emit(fid)

    def put(self, fid: str, name: str, text: str, parents: list[str], perms: list[dict],
            mime: str = "text/markdown") -> None:
        self._rev += 1
        self.files[fid] = DriveFile(fid, name, mime, modified_time=f"t{self._rev}", parents=parents,
                                    web_view_link=f"https://drive.example/{fid}")
        self.content[fid] = text.encode()
        self.perms[fid] = perms
        self._emit(fid)

    def edit(self, fid: str, text: str) -> None:
        self._rev += 1
        self.files[fid].modified_time = f"t{self._rev}"
        self.content[fid] = text.encode()
        self._emit(fid)

    def share(self, fid: str, perms: list[dict]) -> None:
        self.perms[fid] = perms
        self._emit(fid)

    def move(self, fid: str, parents: list[str]) -> None:
        self.files[fid].parents = parents
        self._emit(fid)

    def trash(self, fid: str) -> None:
        self.files[fid].trashed = True
        self._emit(fid)

    def delete(self, fid: str) -> None:
        self.files.pop(fid)
        self._emit(fid, removed=True)


def user(email: str) -> dict:
    return {"type": "user", "role": "reader", "emailAddress": email}


OWNER = {"type": "user", "role": "owner", "emailAddress": "alice@acme.com"}
LEAVE = "# Leave policy\n\n## Annual leave\n\nEmployees get 24 days of paid annual leave each year.\n"
TRAVEL = "# Travel\n\n## Hotels\n\nThe hotel cap in Lisbon is 180 euros per night.\n"
ONCALL = "# On-call\n\n## Paging\n\nPages escalate to the secondary after 10 minutes.\n"


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = VectorStore(AsyncQdrantClient(location=":memory:"), "chunks")
    await store.ensure_collection(get_dense_embedder().dim)
    connector = Connector(id=uuid.uuid4(), tenant_id=TENANT, kind="gdrive", name="Company Drive",
                          config={"folder_ids": ["root"]}, state={}, created_by="admin")
    async with sessions() as db:
        db.add(connector)
        await db.commit()
    drive = FakeDrive()
    drive.folder("root", "Company")

    async def sync(full: bool = False) -> DriveSync:
        s = DriveSync(connector, drive, sessions, store, tmp_path / "uploads")
        connector.state = await s.run(full=full)
        return s

    async def docs() -> dict[str, Document]:
        async with sessions() as db:
            return {d.external_id: d for d in await db.scalars(select(Document))}

    async def sees(principal: Principal, query: str, file_id: str) -> bool:
        d = (await docs()).get(file_id)
        return d is not None and any(c.document_id == str(d.id) for c in await rank(store, principal, query))

    yield drive, sync, docs, sees
    await engine.dispose()


def test_permissions_map_to_principals():
    acl = permissions_to_acl([
        {"type": "user", "role": "owner", "emailAddress": "Alice@Acme.com"},
        {"type": "group", "role": "reader", "emailAddress": "hr@acme.com"},
        {"type": "domain", "role": "reader", "domain": "acme.com"},
        {"type": "anyone", "role": "reader"},
        {"type": "user", "role": "reader", "emailAddress": "gone@acme.com", "deleted": True},
        {"type": "mystery", "role": "reader"},
    ])
    assert acl == ["domain:acme.com", "group:everyone", "group:hr@acme.com", "user:alice@acme.com"]
    assert "domain:acme.com" in ALICE.principals and "domain:acme.com" not in CAROL_OTHER_DOMAIN.principals


async def test_full_sync_respects_scope_types_and_sharing(env):
    drive, sync, docs, sees = env
    drive.folder("hr", "HR", parent="root")
    drive.put("leave", "Leave policy.md", LEAVE, ["hr"], [OWNER, user("bob@acme.com")])
    drive.put("travel", "Travel", TRAVEL, ["root"], [OWNER, {"type": "domain", "role": "reader", "domain": "acme.com"}],
              mime=GOOGLE_DOC_MIME)
    drive.put("photo", "team.png", "binary", ["root"], [OWNER], mime="image/png")
    drive.folder("elsewhere", "Not shared with the connector")
    drive.put("oncall", "On-call.md", ONCALL, ["elsewhere"], [OWNER])

    s = await sync()
    indexed = await docs()
    assert set(indexed) == {"leave", "travel"}
    assert indexed["leave"].title == "Leave policy" and indexed["travel"].mime_type == "text/markdown"
    assert indexed["leave"].acl == ["user:alice@acme.com", "user:bob@acme.com"]
    assert s.stats.added == 2 and s.stats.skipped == 1

    assert await sees(BOB, "how many days of annual leave", "leave")
    assert await sees(BOB, "hotel cap in Lisbon", "travel")  # domain share
    assert not await sees(CAROL_OTHER_DOMAIN, "hotel cap in Lisbon", "travel")
    assert not await sees(CAROL_OTHER_DOMAIN, "how many days of annual leave", "leave")


async def test_incremental_content_vs_sharing_changes(env):
    drive, sync, docs, sees = env
    drive.put("leave", "Leave policy.md", LEAVE, ["root"], [OWNER])
    await sync()
    assert drive.downloads["leave"] == 1
    assert not await sees(BOB, "annual leave days", "leave")

    # Sharing change only: payloads updated, no download, no re-embedding.
    drive.share("leave", [OWNER, user("bob@acme.com")])
    s = await sync()
    assert s.stats.metadata_only == 1 and drive.downloads["leave"] == 1
    assert await sees(BOB, "annual leave days", "leave")

    # Content change: downloaded and re-indexed.
    drive.edit("leave", LEAVE.replace("24 days", "26 days"))
    s = await sync()
    assert s.stats.updated == 1 and drive.downloads["leave"] == 2
    assert (await docs())["leave"].chunk_count == 1

    # Nothing changed: an empty changes page does nothing.
    s = await sync()
    assert s.stats.changes_seen == 0


async def test_removals(env):
    drive, sync, docs, sees = env
    drive.folder("hr", "HR", parent="root")
    for fid, text in [("a", LEAVE), ("b", TRAVEL), ("c", ONCALL)]:
        drive.put(fid, f"{fid}.md", text, ["hr"], [OWNER, user("bob@acme.com")])
    await sync()
    assert set(await docs()) == {"a", "b", "c"}

    drive.trash("a")
    drive.delete("b")
    drive.folder("outside", "Outside")
    drive.move("c", ["outside"])
    s = await sync()
    assert await docs() == {} and s.stats.deleted == 3
    assert not await sees(BOB, "annual leave", "a")


async def test_unsharing_from_connector_removes(env):
    drive, sync, docs, _ = env
    drive.put("leave", "Leave policy.md", LEAVE, ["root"], [OWNER])
    await sync()
    drive.log.append(Change("leave", removed=True, file=None))  # Drive reports loss of access as removed
    await sync()
    assert await docs() == {}


async def test_folder_moves(env):
    drive, sync, docs, _ = env
    drive.folder("archive", "Archive")  # outside scope
    drive.folder("old", "Old policies", parent="archive")
    drive.put("travel", "travel.md", TRAVEL, ["old"], [OWNER])
    drive.folder("hr", "HR", parent="root")
    drive.put("leave", "leave.md", LEAVE, ["hr"], [OWNER])
    drive.put("oncall", "oncall.md", ONCALL, ["hr", "root"], [OWNER])  # lives in two folders
    await sync()
    assert set(await docs()) == {"leave", "oncall"}

    # A folder moved into scope is crawled, even though its files emit no changes themselves.
    drive.files["old"].parents = ["root"]
    drive._emit("old")
    await sync()
    assert set(await docs()) == {"leave", "oncall", "travel"}

    # Trashing a folder drops what is only under it; a file also in another in-scope folder stays.
    drive.trash("hr")
    await sync()
    assert set(await docs()) == {"oncall", "travel"}


async def test_unreadable_sharing_keeps_file_out(env):
    """Drive returns 403 on permissions.list for Viewers; without an ACL the file must not be indexed."""
    drive, sync, docs, _ = env
    drive.put("leave", "Leave policy.md", LEAVE, ["root"], [OWNER])
    await sync()
    assert set(await docs()) == {"leave"}

    async def forbidden(file_id):
        raise httpx.HTTPStatusError("403", request=httpx.Request("GET", "x"), response=httpx.Response(403))

    drive.list_permissions = forbidden
    drive.edit("leave", LEAVE + "\nMore text.\n")
    s = await sync()
    assert await docs() == {} and s.stats.failed == 1
    assert any("Editor" in w for w in s.warnings)


async def test_changes_feed_pagination_and_full_resync(env):
    drive, sync, docs, _ = env
    await sync()  # initial crawl of an empty root, stores the page token
    for i in range(7):  # 7 changes over 4 pages of 2
        drive.put(f"f{i}", f"note {i}.md", f"# Note {i}\n\nItem number {i} is approved.\n", ["root"], [OWNER])
    s = await sync()
    assert s.stats.changes_seen == 7 and len(await docs()) == 7

    # A full resync finds nothing new and re-downloads nothing.
    s = await sync(full=True)
    assert s.stats.unchanged == 7 and s.stats.added == 0
    assert all(n == 1 for n in drive.downloads.values())
