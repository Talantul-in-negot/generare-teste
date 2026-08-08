"""Cloud TTS with an explicit, non-blocking contract.

The API returns the structured answer independently. This provider is only
called when the client asks for audio, and a missing provider is surfaced as
503 rather than silently fabricating an audio response.
"""

from __future__ import annotations

import httpx

from src.core.config import Settings, get_settings


class TtsNotConfiguredError(RuntimeError):
    """TTS was not enabled or has no API key."""


class TtsProviderError(RuntimeError):
    """The configured provider failed to synthesize audio."""


async def synthesize(text: str, *, settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    if settings.tts_provider.lower() != "openai" or not settings.tts_api_key:
        raise TtsNotConfiguredError("TTS_PROVIDER=openai and TTS_API_KEY are required")
    if not text.strip():
        raise ValueError("text must not be empty")
    if len(text) > 4000:
        raise ValueError("text must be at most 4000 characters")

    url = settings.tts_base_url.rstrip("/") + "/audio/speech"
    payload = {
        "model": settings.tts_model,
        "voice": settings.tts_voice,
        "input": text,
        "response_format": "mp3",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.tts_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.tts_api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise TtsProviderError("TTS provider is unreachable") from exc
    if response.status_code >= 400:
        raise TtsProviderError(f"TTS provider returned HTTP {response.status_code}")
    if not response.content:
        raise TtsProviderError("TTS provider returned an empty audio payload")
    return response.content
