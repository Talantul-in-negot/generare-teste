"""Scanned, bounded binary persistence for Buyer Space uploads.

Neo4j retains metadata only.  Bytes go to a workspace/space-scoped directory
in this local implementation, behind the same narrow interface an encrypted
object-store adapter would implement in production.  A binary file is never
stored while scanning is disabled or the scanner is unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.core.config import get_settings


class UploadScanError(RuntimeError):
    """Raised when a file cannot be proven safe to persist."""


@dataclass(frozen=True)
class StoredBuyerUpload:
    object_key: str
    sha256: str
    byte_size: int


def _safe_name(filename: str) -> str:
    name = Path(filename).name.strip().replace("\x00", "")
    if not name or name in {".", ".."}:
        raise UploadScanError("invalid filename")
    return name[:180]


async def _clamav_scan(content: bytes) -> None:
    settings = get_settings()
    if settings.buyer_upload_scanner != "clamav":
        raise UploadScanError("binary uploads require BUYER_UPLOAD_SCANNER=clamav")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.buyer_upload_clamav_host, settings.buyer_upload_clamav_port),
            timeout=5,
        )
        writer.write(b"zINSTREAM\0")
        for offset in range(0, len(content), 64 * 1024):
            part = content[offset: offset + 64 * 1024]
            writer.write(struct.pack("!I", len(part)) + part)
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        response = (await asyncio.wait_for(reader.read(4096), timeout=20)).decode("utf-8", "replace")
        writer.close()
        await writer.wait_closed()
    except (OSError, TimeoutError) as exc:
        raise UploadScanError("malware scanner is unavailable") from exc
    if "OK" not in response or "FOUND" in response:
        raise UploadScanError("malware scanner rejected the file")


async def store_scanned_upload(workspace_id: str, space_id: str, filename: str, content: bytes) -> StoredBuyerUpload:
    settings = get_settings()
    if not content:
        raise UploadScanError("empty binary uploads are not allowed")
    if len(content) > settings.buyer_upload_max_bytes:
        raise UploadScanError("upload exceeds configured maximum size")
    await _clamav_scan(content)
    safe_name = _safe_name(filename)
    object_key = f"{workspace_id}/{space_id}/{uuid4().hex}_{safe_name}"
    target = Path(settings.buyer_upload_storage_dir) / object_key
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, content)
    return StoredBuyerUpload(object_key=object_key, sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content))


async def delete_scanned_upload(object_key: str | None) -> None:
    if not object_key:
        return
    root = Path(get_settings().buyer_upload_storage_dir).resolve()
    target = (root / object_key).resolve()
    if root not in target.parents:
        raise UploadScanError("invalid stored object key")
    if target.exists():
        await asyncio.to_thread(target.unlink)
