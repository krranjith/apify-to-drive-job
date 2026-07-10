#drive_io.py

"""
Generic Google Drive plumbing for the evaluation pipeline. Mirrors the OAuth refresh-token
flow already used by run_apify_to_drive.py / google_search_to_drive.py — kept as its own
self-contained copy here rather than importing across the scripts/evaluation boundary.
"""
from __future__ import annotations
import io
from typing import Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

import config

_service = None

GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."


def _require_env(key: str) -> str:
    import os
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Set {key} in your environment before running.")
    return value


def service():
    global _service
    if _service is None:
        creds = Credentials(
            token=None,
            refresh_token=_require_env("GOOGLE_OAUTH_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=_require_env("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=_require_env("GOOGLE_OAUTH_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        creds.refresh(Request())
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def list_folder(folder_id: str) -> list[dict]:
    """List non-trashed files directly inside a folder (id, name, mimeType, createdTime)."""
    files: list[dict] = []
    page_token = None
    while True:
        resp = (
            service()
            .files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, createdTime)",
                orderBy="createdTime desc",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def find_latest(folder_id: str, name_predicate: Callable[[str], bool]) -> dict | None:
    """Most recently created file in folder_id whose name matches name_predicate, or None."""
    candidates = [f for f in list_folder(folder_id) if name_predicate(f["name"])]
    if not candidates:
        return None
    candidates.sort(key=lambda f: f["createdTime"], reverse=True)
    return candidates[0]


def download_media(file_id: str) -> bytes:
    """Download a real (non-Google-native) binary file's raw bytes."""
    request = service().files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def export_media(file_id: str, mime_type: str) -> bytes:
    """Export a native Google Workspace file (e.g. Sheets) to a plain format like text/csv."""
    request = service().files().export_media(fileId=file_id, mimeType=mime_type)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def download_file(file_id: str, mime_type: str, export_as: str = "text/csv") -> bytes:
    """Download a file's content regardless of whether it's a native Google type or binary."""
    if mime_type.startswith(GOOGLE_NATIVE_PREFIX):
        return export_media(file_id, export_as)
    return download_media(file_id)


def update_media(file_id: str, content: bytes, mime_type: str) -> None:
    """Overwrite a file's content in place, keeping its id/name. Same conversion behavior as
    create() applies — uploading text/csv over a native Sheet re-imports it as a Sheet."""
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
    service().files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()


def create_folder(parent_id: str, name: str) -> str:
    result = (
        service()
        .files()
        .create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return result["id"]


def upload_file(local_path, parent_id: str, name: str | None = None, mime_type: str | None = None) -> str:
    import mimetypes
    from pathlib import Path

    local_path = Path(local_path)
    file_name = name or local_path.name
    mime = mime_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(io.BytesIO(local_path.read_bytes()), mimetype=mime, resumable=False)
    result = (
        service()
        .files()
        .create(
            body={"name": file_name, "parents": [parent_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return result["id"]
