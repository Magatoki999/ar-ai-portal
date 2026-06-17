# services/tts.py
# ─────────────────────────────────────────────────────────────────────────────
# 音声合成（TTS）を担当するサービスモジュール。
# TTS_PROVIDER 環境変数で gemini / elevenlabs / openai を切り替える。
# フォールバック: gemini 失敗 → openai、elevenlabs 失敗 → openai。
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import base64
import io
import wave
import httpx


# ─── OpenAI TTS ───
async def generate_openai_tts(text: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {"model": "tts-1", "input": text, "voice": "nova"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"[TTSエラー] OpenAI TTSに失敗しました: {e}")
    return None


# ─── ElevenLabs TTS ───
async def generate_elevenlabs_voice(text: str) -> str | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"[TTSエラー] ElevenLabsに失敗しました: {e}")
    return None


# ─── Gemini TTS ───
async def generate_gemini_tts(text: str) -> tuple[str, str] | None:
    """
    Gemini Speech Generation API で音声を生成する。
    戻り値: (base64_audio, mime_type) または None
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[TTSエラー] GEMINI_API_KEY が未設定です")
        return None

    voice_name = os.getenv("GEMINI_VOICE_NAME", "Kore")

    # エフェクトタグ等の残留を除去
    clean_text = re.sub(r"\|\|.*?\|\|", "", text).strip()
    clean_text = " ".join(clean_text.split())
    if not clean_text:
        return None

    # キャラクター性を声に反映するスタイル指示
    style_prefix = (
        "小柄で元気な少年のように、好奇心旺盛で感情豊かに、"
        "テンポよくいきいきと話してください: "
    )
    styled_text = style_prefix + clean_text
    print(f"[Gemini TTS] 送信テキスト({len(clean_text)}文字): {clean_text[:80]}")

    model_id = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model_id}:generateContent?key={api_key}"
    )
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": styled_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name}
                }
            },
        },
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, json=payload, headers=headers, timeout=20.0
            )
            if response.status_code == 200:
                res_json = response.json()
                inline_data = (
                    res_json.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("inlineData", {})
                )
                audio_b64 = inline_data.get("data")
                mime_type = inline_data.get(
                    "mimeType", "audio/L16;codec=pcm;rate=24000"
                )
                if audio_b64:
                    print(
                        f"[Gemini TTS] 音声生成成功 voice={voice_name} "
                        f"mimeType={mime_type}"
                    )
                    return audio_b64, mime_type
                else:
                    print(
                        f"[TTSエラー] Gemini TTS レスポンスにaudioデータなし: "
                        f"{res_json}"
                    )
            else:
                print(
                    f"[TTSエラー] Gemini TTS HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
    except Exception as e:
        print(f"[TTSエラー] Gemini TTSに失敗しました: {e}")
    return None


# ─── PCM → WAV 変換ヘルパー ───
async def pcm_to_wav_base64(pcm_b64: str, mime_type: str) -> str:
    """
    GeminiのPCMレスポンス（audio/L16）をWAVに変換してbase64で返す。
    pure Python実装（ffmpeg / pydub不要）。
    """
    rate = 24000
    channels = 1
    for part in mime_type.split(";"):
        part = part.strip().lower()
        if part.startswith("rate="):
            try:
                rate = int(part.split("=")[1].strip())
            except Exception:
                pass
        elif part.startswith("channels="):
            try:
                channels = int(part.split("=")[1].strip())
            except Exception:
                pass
    print(f"[Gemini TTS] WAV変換: rate={rate}Hz channels={channels}")

    pcm_bytes = base64.b64decode(pcm_b64)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16bit
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)
    print(
        f"[Gemini TTS] PCM→WAV変換成功 rate={rate}Hz channels={channels} "
        f"bytes={len(pcm_bytes)}"
    )
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ─── 統合ディスパッチャー ───
async def generate_tts(text: str) -> str | None:
    """
    TTS_PROVIDER 環境変数に応じてプロバイダーを選択する統合関数。
    デフォルト: gemini
    選択肢: gemini / elevenlabs / openai
    """
    provider = os.getenv("TTS_PROVIDER", "gemini").lower()
    print(f"[TTS] provider={provider} / text={text[:20]}...")

    if provider == "elevenlabs":
        audio = await generate_elevenlabs_voice(text)
        return audio or await generate_openai_tts(text)

    if provider == "openai":
        return await generate_openai_tts(text)

    # gemini（デフォルト）
    result = await generate_gemini_tts(text)
    if result:
        audio_b64, mime_type = result
        print(
            f"[TTS分岐] mime_type={mime_type} "
            f"l16check={'l16' in mime_type.lower()}"
        )
        if "l16" in mime_type.lower() or "pcm" in mime_type.lower():
            audio_b64 = await pcm_to_wav_base64(audio_b64, mime_type)
            print(f"[TTS分岐] WAV変換完了 base64長={len(audio_b64)}")
        else:
            print("[TTS分岐] WAV変換スキップ（非PCM）")
        return audio_b64

    print("[TTS] Gemini失敗 → OpenAIにフォールバック")
    return await generate_openai_tts(text)
