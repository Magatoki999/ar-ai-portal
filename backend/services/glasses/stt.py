"""
services/glasses/stt.py

Even G2 microphone raw PCM -> OpenAI speech-to-text.
Input: PCM s16le / 16 kHz / mono (request body)
Output: {"text": "..."}
"""

import asyncio
import io
import json
import os
import uuid
import urllib.request
import urllib.error
import wave

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw G2 PCM (s16le, 16 kHz, mono) in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return buf.getvalue()


def _transcribe_sync(wav_bytes: bytes) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    boundary = f"----RukiRukiG2{uuid.uuid4().hex}"

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    body = bytearray()
    body += field("model", "gpt-4o-mini-transcribe")
    body += field("language", "ja")
    body += (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="g2.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")
    body += wav_bytes
    body += b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI STT HTTP {e.code}: {detail[:500]}") from e
    except Exception as e:
        raise RuntimeError(f"OpenAI STT request failed: {e}") from e

    text = str(payload.get("text") or "").strip()
    return text


@router.post("/api/glasses/transcribe")
async def glasses_transcribe(request: Request):
    pcm = await request.body()

    # 16 kHz * 2 bytes = 32,000 bytes/sec.
    # 極端に短い入力は誤認識しやすいので弾く。
    if len(pcm) < 8000:
        raise HTTPException(status_code=400, detail="audio too short")

    # 30秒を安全上限にする。
    if len(pcm) > 960000:
        raise HTTPException(status_code=413, detail="audio too long")

    try:
        wav_bytes = _pcm_to_wav(pcm)
        text = await asyncio.to_thread(_transcribe_sync, wav_bytes)
        return {"text": text}
    except Exception as e:
        print(f"[G2 STT] {e}")
        raise HTTPException(status_code=500, detail=str(e))
