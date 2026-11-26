"""Text replies for the Magatoki bear via the Meta Model API."""
import os

import httpx

ENDPOINT = "https://api.meta.ai/v1/chat/completions"
MODEL = "muse-spark-1.3"
INSTRUCTIONS = (
    "あなたは腕時計に住む白いクマ、まがとき。親しみやすく、"
    "腕元のスピーカーで聞き取りやすい短い日本語で、原則二文以内で答える。"
    "現在地やスポットの記録が与えられていなければ、知っているふりをしない。"
    "操作を実行したと主張しない。Markdown、記号の連続、長い箇条書きは避ける。"
)

class MuseReplyError(RuntimeError):
    """Failure suitable for translating into an application-level error."""

async def bear_reply(client: httpx.AsyncClient, transcript: str) -> str:
    """Return Muse-generated text. The caller handles authorization and speech."""
    transcript = transcript.strip()
    if not transcript or len(transcript) > 2000:
        raise ValueError("Expected a transcript of 1–2000 characters")
    key = os.environ.get("META_MODEL_API_KEY", "").strip()
    if not key:
        raise MuseReplyError("Meta Model API is not configured")
    try:
        response = await client.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": transcript},
                ],
                "reasoning_effort": "minimal",
                "max_completion_tokens": 512,
            },
            timeout=httpx.Timeout(15, connect=5),
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        reply = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(reply, str) or not reply.strip():
            raise ValueError("Muse returned an incomplete or empty reply")
    except httpx.HTTPStatusError as exc:
        code = "unknown"
        try:
            candidate = exc.response.json()["error"]["code"]
            if (
                isinstance(candidate, str)
                and 1 <= len(candidate) <= 64
                and all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in candidate)
            ):
                code = candidate
        except (ValueError, KeyError, TypeError):
            pass
        raise MuseReplyError(
            f"Meta Model API returned HTTP {exc.response.status_code}; code={code}"
        ) from None
    except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError):
        raise MuseReplyError("Could not retrieve a complete Muse reply") from None
    return reply.strip()
