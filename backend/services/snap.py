# services/snap.py
# ─────────────────────────────────────────────────────────────────────────────
# 「○○とスナップ」コマンドで呼ばれる画像生成サービス。
#   1. context/images/{MEMBER_NAME}.jpg を読み込む
#   2. カメラ映像（背景）とリファレンス画像を Gemini（Nano Banana）に渡して合成
#   3. 生成画像を Supabase memories バケットに保存
# 2026-07-05: gpt-image-1（OpenAI）から gemini-2.5-flash-image（Nano Banana）へ移行。
#   複数画像合成・被写体の一貫性維持に強みがあり、本ユースケース（人物＋背景の合成）に適性が高い。
# ─────────────────────────────────────────────────────────────────────────────
import os
import base64
import pathlib
import random
from datetime import datetime, timedelta, timezone

import httpx
from google import genai
from google.genai import types

# ─── Geminiクライアント（プロセス内で使い回す） ───
_gemini_client: "genai.Client | None" = None


def _get_gemini_client() -> "genai.Client | None":
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


SNAP_IMAGE_MODEL = os.getenv("SNAP_IMAGE_MODEL", "gemini-2.5-flash-image")


async def _generate_via_openai_fallback(
    cam_bytes: bytes, ref_bytes: bytes, prompt: str, ref_bytes_2: bytes | None = None
) -> bytes | None:
    """
    Nano Banana（Gemini）が失敗した場合の保険。OpenAIのgpt-image-1 edit APIで
    同じ合成写真を生成する。OPENAI_API_KEYが無い場合はNoneを返す。
    ref_bytes_2を渡すと2人一緒のスナップにも対応する（2026-07-06追加）。
    """
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("[スナップ] OpenAIフォールバック不可: OPENAI_API_KEY未設定")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            multipart_files = [
                ("image[]", ("background.jpg", cam_bytes, "image/jpeg")),
                ("image[]", ("reference.jpg",  ref_bytes, "image/jpeg")),
            ]
            if ref_bytes_2:
                multipart_files.append(
                    ("image[]", ("reference2.jpg", ref_bytes_2, "image/jpeg"))
                )
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
            print(f"[スナップ] OpenAIフォールバックAPIエラー: {res.status_code} {res.text[:300]}")
            return None

        result  = res.json()
        img_b64 = result["data"][0].get("b64_json")
        if not img_b64:
            print("[スナップ] OpenAIフォールバック: 生成画像データが取得できませんでした")
            return None

        return base64.b64decode(img_b64)

    except Exception as e:
        print(f"[スナップ] OpenAIフォールバック呼び出しエラー: {e}")
        return None


# ─── スナップ用ポーズバリエーション ───
# 毎回ランダムに1つ選んでプロンプトに織り込む。
SNAP_POSES = [
    "making a peace sign (V-sign) with one hand near their face, smiling brightly",
    "waving cheerfully at the camera with one raised hand",
    "standing with arms casually crossed, giving a confident smile",
    "giving a thumbs-up gesture with one hand, energetic expression",
    "leaning slightly toward the camera with both hands forming a heart shape",
    "striking a playful dynamic pose with one hand on hip, looking directly at camera",
    "pointing toward the camera with one finger, playful grin",
    "both hands raised in a small victory pose, joyful expression",
]

# 2026-07-05追加：将来のAI動画生成（被写体参照）に備えた「参照品質」ポーズ。
# SNAP_POSESと違い、①正面以外のアングルを含む ②顔・体を手で隠さない
# ③陽気な表情に偏らない、という3点を意識している。
# REFERENCE_POSE_RATIO の確率でこちらから選ばれ、character_referencesに
# tag="reference" として記録される（SNAP_POSES側は tag="casual"）。
REFERENCE_POSES = [
    "standing naturally facing the camera directly, neutral relaxed expression, arms at sides",
    "standing in a three-quarter turn toward the camera, calm expression, arms at sides",
    "standing in profile (side view), neutral expression, arms at sides",
    "looking slightly away in thought, calm and composed expression, arms at sides",
]

# 参照用ポーズが選ばれる確率（0.0〜1.0）。デフォルト20%。
REFERENCE_POSE_RATIO = float(os.getenv("REFERENCE_POSE_RATIO", "0.2"))

# 2026-07-06追加、2026-07-08改訂：2キャラクター同時スナップ用のポーズ。
# SNAP_POSES/REFERENCE_POSESが1人称の動作記述なのに対し、こちらは
# 「2人の関係性」を記述する必要があるため別プールにしている。
# 2026-07-08：「2人ともカメラ目線で並ぶ」だけだと相互作用が生まれず不自然だった
# （ユーザーからのフィードバック）ため、互いに向き合って会話・やり取りしている
# 描写に統一した。
DUO_POSES = [
    "facing each other in the middle of a lively conversation, one gesturing animatedly "
    "with their hands while talking, the other listening with an amused, engaged expression",
    "standing close together, one pointing at something just out of frame while the other "
    "leans in and looks in the same direction, both curious and focused on it",
    "leaning toward each other laughing together at a shared joke, both slightly bent forward "
    "with genuine laughter",
    "walking side by side mid-conversation, one turned slightly toward the other mid-sentence, "
    "both gesturing naturally as if deep in discussion",
    "one showing something small in their open hands to the other, who is leaning in with "
    "curious, delighted interest",
]


def _resolve_reference_path(member_name: str) -> pathlib.Path | None:
    """
    context/images/ 内のリファレンス画像を大文字小文字を区別せずに探す。
    {member_name}.jpg のほか .jpeg / .png も許容する。
    見つからなければ None を返す。
    """
    images_dir = pathlib.Path("context/images")
    if not images_dir.exists():
        return None

    target = member_name.strip().lower()
    for f in images_dir.iterdir():
        if not f.is_file():
            continue
        stem_lower = f.stem.lower()
        if stem_lower == target and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return f
    return None


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


async def _save_to_reference_library(
    member_name: str, image_url: str, pose: str, tag: str = "casual"
) -> None:
    """
    生成に成功したスナップ画像を、将来の動画生成（被写体参照）用の
    「キャラクター参照画像ライブラリ」（character_referencesテーブル）に記録する。
    2026-07-05新規。ベストエフォート実装（失敗してもスナップ機能自体は失敗させない）。
    tag="casual"：通常のSNAP_POSES。tag="reference"：参照品質を意識したREFERENCE_POSES。
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    )
    if not supabase_url or not supabase_key:
        return

    endpoint = f"{supabase_url}/rest/v1/character_references"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    payload = {
        "member_name": member_name.strip().upper(),
        "image_url":   image_url,
        "pose":        pose,
        "source":      "snap",
        "tag":         tag,
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=payload, headers=headers, timeout=8.0)
        if res.status_code not in (200, 201, 204):
            print(f"[参照ライブラリ] 記録失敗: {res.status_code} {res.text[:200]}")
        else:
            print(f"[参照ライブラリ] {member_name}のポーズ「{pose[:30]}...」を記録しました（tag={tag}）")
    except Exception as e:
        print(f"[参照ライブラリ] 記録エラー（スナップ自体には影響なし）: {e}")


# ─── スナップ生成コア ───
async def generate_snap(
    member_name: str, camera_image_b64: str, member_name_2: str | None = None
) -> tuple[str | None, str | None]:
    """
    スナップ画像を生成して Supabase に保存する。
    member_name_2 を指定すると、2キャラクターが一緒に写るスナップになる
    （2026-07-06追加）。
    戻り値: (image_url, error_message)
        成功時: (url, None)
        失敗時: (None, "エラーメッセージ")
    """
    client = _get_gemini_client()
    if client is None:
        return None, "Gemini APIキー未設定"

    is_duo = bool(member_name_2 and member_name_2.strip())

    # ── 1. リファレンス画像を読み込む（大文字小文字を区別しない） ──
    ref_path = _resolve_reference_path(member_name)
    if ref_path is None:
        print(f"[スナップ] リファレンス画像が見つかりません: {member_name}")
        return None, f"{member_name}のリファレンス画像が見つかりません"

    ref_bytes = ref_path.read_bytes()
    ref_mime  = "image/png" if ref_path.suffix.lower() == ".png" else "image/jpeg"
    print(f"[スナップ] リファレンス画像読み込み: {ref_path} ({len(ref_bytes)}bytes)")

    ref_bytes_2: bytes | None = None
    ref_mime_2:  str | None = None
    if is_duo:
        ref_path_2 = _resolve_reference_path(member_name_2)
        if ref_path_2 is None:
            print(f"[スナップ] リファレンス画像が見つかりません: {member_name_2}")
            return None, f"{member_name_2}のリファレンス画像が見つかりません"
        ref_bytes_2 = ref_path_2.read_bytes()
        ref_mime_2  = "image/png" if ref_path_2.suffix.lower() == ".png" else "image/jpeg"
        print(f"[スナップ] リファレンス画像読み込み（2人目）: {ref_path_2} ({len(ref_bytes_2)}bytes)")

    # ── 2. カメラ画像をバイトに変換 ──
    try:
        cam_b64 = camera_image_b64
        if "," in cam_b64:
            cam_b64 = cam_b64.split(",", 1)[1]
        cam_bytes = base64.b64decode(cam_b64)
    except Exception as e:
        return None, f"カメラ画像のデコードに失敗: {e}"

    # ── 3. Gemini（Nano Banana）で画像合成 ──
    if is_duo:
        # 2人一緒のスナップ：DUO_POSESから選ぶ。参照ライブラリ用のcasual/reference
        # 区別は1人用スナップの概念なので、ここでは使わない（pose_tagはNone扱い）。
        use_reference_pose = False
        pose = random.choice(DUO_POSES)
        pose_tag = "duo"
        prompt = (
            "The two people shown in the reference images are standing together in "
            "the scene shown in the background photo, interacting with each other: "
            f"{pose}. "
            "This should look like a candid, unposed snapshot caught mid-moment — neither "
            "person needs to be looking at the camera. "
            "Create a realistic photo where both people blend naturally into the environment. "
            "Maintain each person's face, hairstyle, and clothing from their respective "
            "reference image as accurately as possible. Do not mix or swap their outfits or "
            "features between the two characters. "
            "\n\nCRITICAL FOR REALISM (avoid a 'pasted sticker' look):\n"
            "- Match the exact direction, color temperature, and softness of the light source "
            "visible in the background photo. If the room light is warm and soft, the light on "
            "both people must be equally warm and soft, not studio-flat lighting.\n"
            "- Cast a soft contact shadow from each person onto the floor/wall behind them, "
            "consistent with the background's existing shadows.\n"
            "- Match the background photo's camera grain, sharpness, and depth of field so both "
            "people look like they were captured by the same camera at the same time, not "
            "composited from a separate studio render.\n"
            "- Both people should be standing firmly grounded on the visible floor, not floating "
            "slightly above it.\n\n"
            "CRITICAL FOR SCALE (avoid a 'tiny figurine placed on the floor' look):\n"
            "- Both characters are child-sized humans, not miniature figurines or toys. Scale "
            "them believably against real objects in the background (doorways, furniture, "
            "shelves, bags) as a real child of that height would appear — this is usually "
            "taller and closer to the camera than a small object placed on the ground.\n"
            "- Do not place them tucked into a small gap next to furniture as if they were an "
            "object on a shelf; they should occupy the scene as living people standing on the "
            "main floor area.\n\n"
            "Make it look like a candid photograph, full of energy and fun.\n\n"
            "IMPORTANT OUTPUT RULES:\n"
            "- Output exactly ONE image: the final composited photo described above.\n"
            "- Do NOT output either reference image or the background image unmodified or as "
            "a separate image (e.g. no character sheet, no closeup portrait, no isolated "
            "render on a plain background). Only the single blended scene counts as output."
        )
    else:
        # 2026-07-05更新：REFERENCE_POSE_RATIOの確率で参照品質ポーズを混ぜる
        use_reference_pose = random.random() < REFERENCE_POSE_RATIO
        pose = random.choice(REFERENCE_POSES) if use_reference_pose else random.choice(SNAP_POSES)
        pose_tag = "reference" if use_reference_pose else "casual"
        prompt = (
            "The person shown in the reference image is naturally posing in the scene "
            "shown in the background photo, "
            f"{pose}. "
            "Create a realistic photo where the person blends naturally into the environment. "
            "Maintain the person's face, hairstyle, and clothing from the reference image "
            "as accurately as possible. "
            "The lighting and perspective should match the background scene. "
            "Make it look like a candid photograph taken together, full of energy and fun.\n\n"
            "IMPORTANT OUTPUT RULES:\n"
            "- Output exactly ONE image: the final composited photo described above.\n"
            "- Do NOT output the reference image or the background image unmodified or as "
            "a separate image (e.g. no character sheet, no closeup portrait, no isolated "
            "render on a plain background). Only the single blended scene counts as output."
        )
    print(f"[スナップ] 選択ポーズ: {pose}")
    try:
        contents: list = [
            types.Part.from_bytes(data=cam_bytes, mime_type="image/jpeg"),
            types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime),
        ]
        if is_duo:
            contents.append(types.Part.from_bytes(data=ref_bytes_2, mime_type=ref_mime_2))
        contents.append(prompt)

        response = await client.aio.models.generate_content(
            model=SNAP_IMAGE_MODEL,
            contents=contents,
        )

        # 複数の画像パートが返ってくることがあるため（リファレンスの再生成が
        # 混ざって返る場合がある）、最初の1枚を無条件採用せず、
        # 最もバイトサイズが大きい画像（=背景を含む合成写真である可能性が高い）を選ぶ。
        generated_bytes = None
        best_size = -1
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            for part in candidates[0].content.parts:
                inline = getattr(part, "inline_data", None)
                if not inline or not inline.data:
                    continue
                # SDKバージョンによりbytesのままの場合とbase64文字列の場合がある
                data_bytes = (
                    inline.data if isinstance(inline.data, bytes)
                    else base64.b64decode(inline.data)
                )
                if len(data_bytes) > best_size:
                    best_size = len(data_bytes)
                    generated_bytes = data_bytes

        if not generated_bytes:
            print(f"[スナップ] Gemini応答に画像データがありません: {response} → OpenAIにフォールバック")
            generated_bytes = await _generate_via_openai_fallback(
                cam_bytes, ref_bytes, prompt, ref_bytes_2=ref_bytes_2
            )
            if not generated_bytes:
                return None, "生成画像データが取得できませんでした（Gemini/OpenAIともに失敗）"
            print(f"[スナップ] OpenAIフォールバックで画像生成成功: {len(generated_bytes)}bytes")
        else:
            num_image_parts = sum(
                1 for p in (candidates[0].content.parts if candidates else [])
                if getattr(p, "inline_data", None) and p.inline_data.data
            )
            if num_image_parts > 1:
                print(
                    f"[スナップ] ⚠️ 画像パートが{num_image_parts}枚返されました"
                    f"（最大サイズ={best_size}bytesを採用）。"
                    "リファレンス再生成が混ざっている可能性があります。"
                )
            print(f"[スナップ] 画像生成成功: {len(generated_bytes)}bytes")

    except Exception as e:
        print(f"[スナップ] Gemini呼び出しエラー: {e} → OpenAIにフォールバック")
        generated_bytes = await _generate_via_openai_fallback(
            cam_bytes, ref_bytes, prompt, ref_bytes_2=ref_bytes_2
        )
        if not generated_bytes:
            return None, f"画像生成エラー: Gemini({e})、OpenAIフォールバックも失敗"
        print(f"[スナップ] OpenAIフォールバックで画像生成成功: {len(generated_bytes)}bytes")

    # ── 4. Supabase memories バケットに保存 ──
    JST = timezone(timedelta(hours=+9))
    ts  = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    safe_member_name = member_name.strip().upper().replace(" ", "_")
    if is_duo:
        safe_member_name_2 = member_name_2.strip().upper().replace(" ", "_")
        filename = f"snap_{safe_member_name}_{safe_member_name_2}_{ts}.jpg"
    else:
        filename = f"snap_{safe_member_name}_{ts}.jpg"
    image_url = await upload_to_supabase_storage(generated_bytes, filename)

    if not image_url:
        return None, "Supabaseへの保存に失敗しました"

    if is_duo:
        # 2人が写った合成画像は「単体キャラクターの参照素材」としては使いにくいため、
        # 参照ライブラリには記録しない（各キャラクター単体のスナップの時だけ記録する）。
        print("[スナップ] 2人一緒のスナップのため参照ライブラリへの記録はスキップします")
    else:
        # 将来の動画生成（被写体参照）に備え、成功したポーズを参照ライブラリに記録する
        await _save_to_reference_library(member_name, image_url, pose, tag=pose_tag)

    return image_url, None
