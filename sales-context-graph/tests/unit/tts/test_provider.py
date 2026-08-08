from __future__ import annotations

import httpx
import pytest

from src.core.config import Settings
from src.tts.provider import TtsNotConfiguredError, synthesize


@pytest.mark.asyncio
async def test_synthesize_requires_explicit_configuration() -> None:
    with pytest.raises(TtsNotConfiguredError):
        await synthesize("A grounded answer", settings=Settings(_env_file=None))


@pytest.mark.asyncio
async def test_synthesize_posts_openai_speech_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        content = b"ID3-audio"

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, str]) -> FakeResponse:
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await synthesize(
        "Send the pricing calculator.",
        settings=Settings(
            _env_file=None,
            tts_provider="openai",
            tts_api_key="test-key",
            tts_voice="nova",
        ),
    )

    assert result == b"ID3-audio"
    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["json"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "nova",
        "input": "Send the pricing calculator.",
        "response_format": "mp3",
    }
