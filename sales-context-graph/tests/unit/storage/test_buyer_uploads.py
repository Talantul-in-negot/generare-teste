from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.storage import buyer_uploads

pytestmark = pytest.mark.asyncio


async def test_scanned_upload_stores_metadata_and_deletes_bytes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BUYER_UPLOAD_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()

    async def scanned(_: bytes) -> None:
        return None

    monkeypatch.setattr(buyer_uploads, "_clamav_scan", scanned)
    stored = await buyer_uploads.store_scanned_upload(
        "ws-1", "space-1", "../../proposal.pdf", b"%PDF-safe-test"
    )

    assert stored.byte_size == len(b"%PDF-safe-test")
    assert "/" in stored.object_key
    assert ".." not in stored.object_key
    assert (tmp_path / stored.object_key).read_bytes() == b"%PDF-safe-test"
    await buyer_uploads.delete_scanned_upload(stored.object_key)
    assert not (tmp_path / stored.object_key).exists()
    get_settings.cache_clear()


async def test_binary_upload_fails_closed_without_scanner(monkeypatch) -> None:
    monkeypatch.setenv("BUYER_UPLOAD_SCANNER", "disabled")
    get_settings.cache_clear()
    with pytest.raises(buyer_uploads.UploadScanError, match="BUYER_UPLOAD_SCANNER"):
        await buyer_uploads.store_scanned_upload("ws-1", "space-1", "proposal.pdf", b"safe")
    get_settings.cache_clear()
