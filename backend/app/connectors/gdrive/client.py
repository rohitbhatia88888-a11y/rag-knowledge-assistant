"""Thin async client for the Google Drive v3 REST API, plus the interface the sync engine depends on."""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import google.auth
import httpx
from google.auth import impersonated_credentials as impersonated
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account

API = "https://www.googleapis.com/drive/v3"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FILE_FIELDS = "id,name,mimeType,modifiedTime,md5Checksum,parents,trashed,webViewLink,size"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
# Shared-drive files need these on every call, or they are silently left out.
ALL_DRIVES = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str = ""
    md5: str | None = None
    parents: list[str] = field(default_factory=list)
    trashed: bool = False
    web_view_link: str | None = None
    size: int | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def version(self) -> str:
        """Changes when content changes. Google Docs have no md5, so fall back to modifiedTime."""
        return self.md5 or self.modified_time

    @classmethod
    def from_api(cls, f: dict) -> "DriveFile":
        return cls(
            id=f["id"],
            name=f.get("name", ""),
            mime_type=f.get("mimeType", ""),
            modified_time=f.get("modifiedTime", ""),
            md5=f.get("md5Checksum"),
            parents=f.get("parents", []),
            trashed=f.get("trashed", False),
            web_view_link=f.get("webViewLink"),
            size=int(f["size"]) if f.get("size") else None,
        )


@dataclass
class Change:
    file_id: str
    removed: bool  # gone or no longer visible to us
    file: DriveFile | None


class DriveClient(Protocol):
    async def start_page_token(self) -> str: ...
    async def list_children(self, folder_id: str) -> list[DriveFile]: ...
    async def list_changes(self, page_token: str) -> tuple[list[Change], str | None, str | None]:
        """Returns (changes, next_page_token, new_start_page_token); exactly one token is set."""
        ...
    async def list_permissions(self, file_id: str) -> list[dict]: ...
    async def download(self, file: DriveFile) -> bytes: ...


def key_file_credentials(path: str, subject: str | None = None) -> Credentials:
    """Service account key file; `subject` impersonates a Workspace user (domain-wide delegation)."""
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    return creds.with_subject(subject) if subject else creds


def impersonated_credentials(target_service_account: str) -> Credentials:
    """Keyless: act as the service account using Application Default Credentials.

    The caller (e.g. `gcloud auth application-default login`, or a workload identity in production)
    needs roles/iam.serviceAccountTokenCreator on the service account. Tokens last an hour and no
    key file exists to leak, which is why orgs that block key creation still allow this.
    """
    source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return impersonated.Credentials(
        source_credentials=source, target_principal=target_service_account, target_scopes=SCOPES, lifetime=3600
    )


class GoogleDriveClient:
    def __init__(self, credentials: Credentials, timeout: float = 60) -> None:
        self._creds = credentials
        self._http = httpx.AsyncClient(base_url=API, timeout=timeout)
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict:
        async with self._lock:
            if not self._creds.valid:
                await asyncio.to_thread(self._creds.refresh, Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(5):
            resp = await self._http.get(path, params=params, headers=await self._headers())
            # Drive rate-limits with 403 userRateLimitExceeded or 429; back off and retry.
            if resp.status_code in (429, 500, 502, 503) or (
                resp.status_code == 403 and "rateLimitExceeded" in resp.text
            ):
                await asyncio.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    async def start_page_token(self) -> str:
        resp = await self._get("/changes/startPageToken", {"supportsAllDrives": "true"})
        return resp.json()["startPageToken"]

    async def list_children(self, folder_id: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "pageSize": "1000",
            **ALL_DRIVES,
        }
        while True:
            data = (await self._get("/files", params)).json()
            out.extend(DriveFile.from_api(f) for f in data.get("files", []))
            if not (token := data.get("nextPageToken")):
                return out
            params["pageToken"] = token

    async def list_changes(self, page_token: str) -> tuple[list[Change], str | None, str | None]:
        data = (
            await self._get(
                "/changes",
                {
                    "pageToken": page_token,
                    "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
                    "includeRemoved": "true",
                    "pageSize": "1000",
                    **ALL_DRIVES,
                },
            )
        ).json()
        changes = [
            Change(
                file_id=c["fileId"],
                removed=c.get("removed", False),
                file=DriveFile.from_api(c["file"]) if c.get("file") else None,
            )
            for c in data.get("changes", [])
            if "fileId" in c
        ]
        return changes, data.get("nextPageToken"), data.get("newStartPageToken")

    async def list_permissions(self, file_id: str) -> list[dict]:
        out: list[dict] = []
        params = {
            "fields": "nextPageToken,permissions(id,type,role,emailAddress,domain,deleted)",
            "supportsAllDrives": "true",
        }
        while True:
            data = (await self._get(f"/files/{file_id}/permissions", params)).json()
            out.extend(data.get("permissions", []))
            if not (token := data.get("nextPageToken")):
                return out
            params["pageToken"] = token

    async def download(self, file: DriveFile) -> bytes:
        if file.mime_type == GOOGLE_DOC_MIME:
            resp = await self._get(f"/files/{file.id}/export", {"mimeType": "text/markdown"})
        else:
            resp = await self._get(f"/files/{file.id}", {"alt": "media", "supportsAllDrives": "true"})
        return resp.content
