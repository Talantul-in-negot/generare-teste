"""Optional audio rendering for already-grounded assistant text."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from api.dependencies import verify_api_key
from src.tts.provider import TtsNotConfiguredError, TtsProviderError, synthesize

router = APIRouter(prefix="/api/v1", tags=["tts"])


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/tts", response_class=Response)
async def tts(body: TtsRequest, _workspace_id: str = Depends(verify_api_key)) -> Response:
    """Return MP3 audio for a user-selected answer or narrative.

    The caller should render the text first and request this endpoint only
    when audio is enabled. This keeps answer latency independent of TTS.
    """
    try:
        audio = await synthesize(body.text)
    except TtsNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TtsProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=300"})
