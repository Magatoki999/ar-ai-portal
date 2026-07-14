"""
services/prompt_builder.py

scene_references + ruki_mind（マインドプロファイル）+ _PromptBuilder/00_builder.md を
組み合わせて、AI動画生成用プロンプト（日本語/英語）を生成する。

既存の character_bible.py と同じ方針を踏襲：
- 会話フロー（services/memory.py 等）の既存関数には一切触れない
- 独自のデータ取得関数をこのファイル内に閉じて持つ
- Supabase操作はPostgREST直叩き（httpx）。ORMは使わない
- 新形式キー（sb_secret_...）前提で apikey ヘッダーのみ送る
- 日付・自由文字列をcrudeにcrクエリへ埋め込まない（quote()で必ずエンコード）
"""

import os
import glob
import re
from datetime import datetime, timezone
from urllib.parse import quote
from pathlib import Path

import httpx

from services.resilient_llm import build_fast_llm  # 既存のResilientLLMファクトリを再利用
from services.character_bible import _RUKI_MIND_DIR  # ruki_mind/のパスを一元管理箇所から流用

# Router/Evaluator等と同じ考え方：コスト最小のfastモデルで十分（構造化出力ではないため素直な生成）
llm_fast = build_fast_llm(temperature=0.7, name="PROMPT-BUILDER-LLM")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")

# ruki_mind/ の実際の配置場所は character_bible.py の _RUKI_MIND_DIR と同一のものを使う
# （二重管理を避けるため、独自のパス解決ロジックは持たない）
RUKI_MIND_DIR = _RUKI_MIND_DIR
BUILDER_RULES_PATH = RUKI_MIND_DIR / "_PromptBuilder" / "00_builder.md"


def _pb_headers() -> dict:
    """books.pyの_books_headers()と同じ考え方：apikeyのみを送る（sb_secret_形式に対応）。"""
    return {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────────────────────────────────

async def _fetch_scene_reference(scene_id: str) -> dict | None:
    """scene_references から1件取得する。"""
    url = f"{SUPABASE_URL}/rest/v1/scene_references"
    params = {"id": f"eq.{scene_id}", "select": "*"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    return rows[0] if rows else None


async def list_recent_scene_references(limit: int = 20) -> list[dict]:
    """直近のscene_referencesを新しい順で返す（スマホから選ぶ一覧表示用）。"""
    url = f"{SUPABASE_URL}/rest/v1/scene_references"
    params = {
        "select": "id,member_names,pose,is_duo,created_at",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()


def _load_latest_mind_profile() -> str:
    """ruki_mind/YYYY-MM.md のうち最新のものを読み込む。無ければ空文字を返す。"""
    pattern = str(RUKI_MIND_DIR / "20*-*.md")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        return ""
    latest_path = candidates[-1]
    return Path(latest_path).read_text(encoding="utf-8")


def _load_builder_rules() -> str:
    """00_builder.md を読み込む。無ければ空文字を返す（呼び出し側でエラーにする）。"""
    if not BUILDER_RULES_PATH.exists():
        return ""
    return BUILDER_RULES_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# プロンプト生成
# ─────────────────────────────────────────────────────────────────────────

_GENERATION_SYSTEM_PROMPT = """あなたはAI動画生成プロンプトの専門家です。
以下の3つの情報を踏まえて、指定されたシーンをAI動画生成（Seedance 2.0 / Kling / Wan 等）
向けの具体的なプロンプトに変換してください。

【ルキルキの現在の人格プロファイル】
{mind_profile}

【映像表現の具体性ルール（00_builder.md）】
{builder_rules}

【変換対象のシーン】
- 動作の記述: {pose_text}
- ジャンル: {genre}

出力条件:
- まず日本語プロンプト、次に英語プロンプトの順で出力する
- 各プロンプトの前に「## 日本語プロンプト」「## English Prompt」という見出しを付ける
- 禁止語（映画的・美しい・自然な・雰囲気のある等）を単独では使わない
- 必ず6要素（誰が・どこで・何をして・どちらを向いて・カメラ・時間変化）を含める
- ルキルキ固有のルール（感情の出し方・姿勢・カメラワーク）を反映する
- 動作（pose_text）自体は変更せず、表情・感情表現のみ人格ルール側の抑制基準に合わせる
- 否定命令だけに依存せず、望ましい状態を具体的に書く
- 必要以上に長くしない（目安: 日本語200字以内）
- 場所・背景がシーン記述に含まれない場合は、人格プロファイルの「空間・背景の傾向」から選ぶ
"""


def _parse_llm_output(raw_text: str) -> tuple[str, str]:
    """LLM出力から日本語プロンプトと英語プロンプトを分離する。"""
    ja_match = re.search(r"##\s*日本語プロンプト\s*\n(.+?)(?=##\s*English Prompt|\Z)", raw_text, re.S)
    en_match = re.search(r"##\s*English Prompt\s*\n(.+)", raw_text, re.S)
    prompt_ja = ja_match.group(1).strip() if ja_match else raw_text.strip()
    prompt_en = en_match.group(1).strip() if en_match else ""
    return prompt_ja, prompt_en


async def generate_video_prompt(scene_id: str, genre: str = "日常") -> dict:
    """
    scene_id を指定して、00_builder.mdのルールに沿ったプロンプトを生成し、
    video_prompts テーブルへ保存する。戻り値は保存したレコード。
    """
    scene = await _fetch_scene_reference(scene_id)
    if scene is None:
        raise ValueError(f"scene_reference id={scene_id} が見つかりません")

    builder_rules = _load_builder_rules()
    if not builder_rules:
        raise RuntimeError(
            f"00_builder.md が見つかりません（{BUILDER_RULES_PATH}）。"
            "character_bible.pyの_RUKI_MIND_DIRが正しいパスを指しているか確認してください。"
        )
    mind_profile = _load_latest_mind_profile()

    pose_text = scene.get("pose", "")
    prompt_text = _GENERATION_SYSTEM_PROMPT.format(
        mind_profile=mind_profile or "（プロファイル未生成）",
        builder_rules=builder_rules,
        pose_text=pose_text,
        genre=genre,
    )

    response = await llm_fast.ainvoke(prompt_text)
    raw_text = response.content
    prompt_ja, prompt_en = _parse_llm_output(raw_text)

    # 00_builder.mdの更新ログ日付を version として記録。
    # 更新ログテーブルは日付順に行が並ぶ想定なので、最初にヒットした日付ではなく
    # 最後にヒットした日付（＝最新の更新）を拾う。
    version_matches = re.findall(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", builder_rules)
    builder_version = version_matches[-1] if version_matches else None

    saved = await _save_video_prompt(
        scene_reference_id=scene_id,
        genre=genre,
        prompt_ja=prompt_ja,
        prompt_en=prompt_en,
        builder_version=builder_version,
    )
    return saved


async def _save_video_prompt(
    scene_reference_id: str,
    genre: str,
    prompt_ja: str,
    prompt_en: str,
    builder_version: str | None,
) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/video_prompts"
    payload = {
        "scene_reference_id": scene_reference_id,
        "genre": genre,
        "prompt_ja": prompt_ja,
        "prompt_en": prompt_en,
        "builder_version": builder_version,
        "status": "generated",
    }
    headers = _pb_headers()
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    return rows[0] if rows else payload


# ─────────────────────────────────────────────────────────────────────────
# 蓄積したプロンプトの閲覧・結果の書き戻し
# ─────────────────────────────────────────────────────────────────────────

async def list_video_prompts(status: str | None = None, limit: int = 20) -> list[dict]:
    """蓄積されたvideo_promptsを新しい順で返す。statusで絞り込み可能。"""
    url = f"{SUPABASE_URL}/rest/v1/video_prompts"
    params = {
        "select": "*",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    if status:
        params["status"] = f"eq.{quote(status, safe='')}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()


async def update_video_prompt_result(
    prompt_id: int,
    tested_model: str,
    result_video_url: str | None = None,
    result_notes: str | None = None,
    status: str = "tested",
) -> dict:
    """
    実際にKling/Pollo AI等で試した後、結果を書き戻す。
    これにより「プロンプト→実際の結果」のペアがvideo_promptsに蓄積されていく。
    """
    url = f"{SUPABASE_URL}/rest/v1/video_prompts"
    params = {"id": f"eq.{prompt_id}"}
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "tested_model": tested_model,
        "result_video_url": result_video_url,
        "result_notes": result_notes,
        "status": status,
        "tested_at": now_iso,
    }
    headers = _pb_headers()
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient() as client:
        resp = await client.patch(url, headers=headers, params=params, json=payload, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    return rows[0] if rows else payload
