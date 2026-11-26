"""Private, single-device voice endpoint for the Magatoki clock."""
import base64
import binascii
import hmac
import os

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.magatoki_muse import MuseReplyError, bear_reply
from services.tts import generate_tts

router = APIRouter()

class BearMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

@router.post("/api/magatoki-bear/chat")
async def bear_chat(
    payload: BearMessage, x_magatoki_bear_token: str = Header(default="")
):
    secret = os.environ.get("MAGATOKI_BEAR_ACCESS_TOKEN", "").strip().encode()
    if len(secret) < 32:
        raise HTTPException(503, "Bear chat is not configured")
    if not hmac.compare_digest(secret, x_magatoki_bear_token.encode()):
        raise HTTPException(401, "Bear access token required")
    if not payload.message.strip():
        raise HTTPException(422, "Message is empty")
    try:
        async with httpx.AsyncClient() as client:
            reply = await bear_reply(client, payload.message)
    except MuseReplyError as exc:
        raise HTTPException(502, "Muse could not answer") from exc
    audio = await generate_tts(reply)
    if not audio:
        raise HTTPException(502, "Speech synthesis failed")
    try:
        prefix = base64.b64decode(audio[:16], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(502, "Invalid speech audio") from exc
    mime = "audio/wav" if prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE" else "audio/mpeg"
    return {"reply": reply, "audio_data": audio, "audio_mime": mime}
