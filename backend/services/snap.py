# services/snap.py
# ─────────────────────────────────────────────────────────────────────────────
# 「○○とスナップ」コマンドで呼ばれる画像生成サービス。
#   1. context/images/{MEMBER_NAME}.jpg を読み込む
#   2. カメラ映像（背景）とリファレンス画像を gpt-image-1 edit に渡す
#   3. 生成画像を Supabase memories バケットに保存
# ─────────────────────────────────────────────────────────────────────────────
import os
import base64
import pathlib
from datetime import datetime, timedelta, timezone

import httpx


# ─── Supabase ストレージ ───
async def upload_to_supabase_storage(
    image_bytes: bytes, filename: str
) -> str | None:
    """生成画像を Supabase memories バケットにアップロードして public URL を返す。"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    )
    if not supabase_url or not supabase_key:
        return None

    upload_url = f"{supabase_url}/storage/v1/object/memories/{filename}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                upload_url, content=image_bytes, headers=headers, timeout=30.0
            )
            if res.status_code in (200, 201):
                public_url = (
                    f"{supabase_url}/storage/v1/object/public/memories/{filename}"
                )
                print(f"[スナップ] Supabase保存完了: {public_url}")
                return public_url
            else:
                print(
                    f"[スナップ] Supabase保存失敗: "
                    f"{res.status_code} {res.text[:200]}"
                )
    except Exception as e:
        print(f"[スナップ] Supabase保存エラー: {e}")
    return None


# ─── スナップ生成コア ───
async def generate_snap(
    member_name: str, camera_image_b64: str
) -> tuple[str | None, str | None]:
    """
    スナップ画像を生成して Supabase に保存する。
    戻り値: (image_url, error_message)
        成功時: (url, None)
        失敗時: (None, "エラーメッセージ")
    """
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return None, "OpenAI APIキー未設定"

    # ── 1. リファレンス画像を読み込む ──
    member_upper = member_name.upper()
    ref_path = pathlib.Path(f"context/images/{member_upper}.jpg")
    if not ref_path.exists():
        ref_path_lower = pathlib.Path(f"context/images/{member_name}.jpg")
        if ref_path_lower.exists():
            ref_path = ref_path_lower
        else:
            print(f"[スナップ] リファレンス画像が見つかりません: {member_upper}.jpg")
            return None, f"{member_name}のリファレンス画像が見つかりません"

    ref_bytes = ref_path.read_bytes()
    print(f"[スナップ] リファレンス画像読み込み: {ref_path} ({len(ref_bytes)}bytes)")

    # ── 2. カメラ画像をバイトに変換 ──
    try:
        cam_b64 = camera_image_b64
        if "," in cam_b64:
            cam_b64 = cam_b64.split(",", 1)[1]
        cam_bytes = base64.b64decode(cam_b64)
    except Exception as e:
        return None, f"カメラ画像のデコードに失敗: {e}"

    # ── 3. gpt-image-1 edit で画像生成 ──
    prompt = (
        "The person shown in the reference image is naturally standing in the scene "
        "shown in the background photo. "
        "Create a realistic photo where the person blends naturally into the environment. "
        "Maintain the person's face, hairstyle, and clothing from the reference image "
        "as accurately as possible. "
        "The lighting and perspective should match the background scene. "
        "Make it look like a candid photograph taken together."
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            multipart_files = [
                ("image[]", ("background.jpg", cam_bytes, "image/jpeg")),
                ("image[]", ("reference.jpg",  ref_bytes, "image/jpeg")),
            ]
            data = {
                "model":   "gpt-image-1",
                "prompt":  prompt,
                "n":       "1",
                "size":    "1024x1024",
                "quality": "medium",
            }
            res = await client.post(
                "https://api.openai.com/v1/images/edits",
                headers={"Authorization": f"Bearer {openai_api_key}"},
                files=multipart_files,
                data=data,
            )

        if res.status_code != 200:
            print(f"[スナップ] OpenAI API エラー: {res.status_code} {res.text[:300]}")
            return None, f"画像生成に失敗しました: {res.status_code}"

        result   = res.json()
        img_b64  = result["data"][0].get("b64_json") or result["data"][0].get("url")
        if not img_b64:
            return None, "生成画像データが取得できませんでした"

        generated_bytes = base64.b64decode(img_b64)
        print(f"[スナップ] 画像生成成功: {len(generated_bytes)}bytes")

    except Exception as e:
        print(f"[スナップ] OpenAI呼び出しエラー: {e}")
        return None, f"画像生成エラー: {e}"

    # ── 4. Supabase memories バケットに保存 ──
    JST = timezone(timedelta(hours=+9))
    ts  = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    filename  = f"snap_{member_upper}_{ts}.jpg"
    image_url = await upload_to_supabase_storage(generated_bytes, filename)

    if not image_url:
        return None, "Supabaseへの保存に失敗しました"

    return image_url, None
