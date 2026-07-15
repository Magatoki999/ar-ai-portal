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

import glob
import re
import base64
from datetime import datetime, timezone
from urllib.parse import quote
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from services.resilient_llm import build_fast_llm  # 既存のResilientLLMファクトリを再利用
from services.character_bible import _RUKI_MIND_DIR  # ruki_mind/のパスを一元管理箇所から流用
from services.memory import _sb  # books.pyと同じ接続情報ヘルパーを再利用

# Router/Evaluator等と同じ考え方：コスト最小のfastモデルで十分（構造化出力ではないため素直な生成）
llm_fast = build_fast_llm(temperature=0.7, name="PROMPT-BUILDER-LLM")

# ruki_mind/ の実際の配置場所は character_bible.py の _RUKI_MIND_DIR と同一のものを使う
# （二重管理を避けるため、独自のパス解決ロジックは持たない）
RUKI_MIND_DIR = _RUKI_MIND_DIR
BUILDER_RULES_PATH = RUKI_MIND_DIR / "_PromptBuilder" / "00_builder.md"


def _pb_headers() -> dict:
    """books.pyの_books_headers()と同じ考え方：apikeyのみを送る（sb_secret_形式に対応）。"""
    _, key = _sb()
    return {
        "apikey": key,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────────────────────────────────

async def _fetch_scene_reference(scene_id: str) -> dict | None:
    """scene_references から1件取得する。"""
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/scene_references"
    params = {"id": f"eq.{scene_id}", "select": "*"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    return rows[0] if rows else None


async def list_recent_scene_references(limit: int = 20) -> list[dict]:
    """直近のscene_referencesを新しい順で返す（スマホから選ぶ一覧表示用）。"""
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/scene_references"
    params = {
        "select": "id,member_names,pose,is_duo,created_at,image_url",
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


async def _fetch_image_as_data_url(image_url: str) -> str | None:
    """
    scene_referencesのimage_url（実際のスナップ写真）を取得し、
    LLMへ渡せるdata URL形式（base64）に変換する。取得失敗時はNoneを返す
    （呼び出し側は画像無しの従来フローにフォールバックする）。
    """
    if not image_url:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(image_url, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            b64 = base64.b64encode(resp.content).decode("utf-8")
            return f"data:{content_type};base64,{b64}"
    except Exception:
        # 写真取得に失敗しても致命的にせず、テキストのみのフローへ落とす
        return None


def _load_ruki_reference_image_as_data_url(filename: str = "ruki_bust_neutral.png") -> str | None:
    """
    ruki_mind/reference_images/ からルキルキ本人の外見リファレンス画像を
    ローカルファイルとして読み込み、data URL化する。
    他キャラのシーンにルキルキを主演させたい場合（写真の人物とは別人を描写したい場合）に、
    背景写真とは別にこの画像を渡すことで、外見の取り違えを防ぐ。
    """
    path = RUKI_MIND_DIR / "reference_images" / filename
    if not path.exists():
        return None
    try:
        ext = path.suffix.lstrip(".").lower()
        mime = "image/png" if ext == "png" else "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# プロンプト生成
# ─────────────────────────────────────────────────────────────────────────

_GENERATION_SYSTEM_PROMPT = """あなたはAI動画生成プロンプトの専門家です。
以下の情報を踏まえて、指定されたシーンをAI動画生成（Seedance 2.0 / Kling / Wan 等）
向けの具体的なプロンプトに変換してください。

【ルキルキの現在の人格プロファイル】
{mind_profile}

【映像表現の具体性ルール（00_builder.md）】
{builder_rules}

【変換対象のシーン】
- 動作の記述: {pose_text}
- ジャンル: {genre}
{image_instruction}

出力条件:
- まず日本語プロンプト、次に英語プロンプトの順で出力する
- 各プロンプトの前に「## 日本語プロンプト」「## English Prompt」という見出しを付ける
- 禁止語（映画的・美しい・自然な・雰囲気のある等）を単独では使わない
- 必ず6要素（誰が・どこで・何をして・どちらを向いて・カメラ・時間変化）を含める
- ルキルキ固有のルール（感情の出し方・姿勢・カメラワーク）を反映する
- 動作（pose_text）自体は変更せず、表情・感情表現のみ人格ルール側の抑制基準に合わせる
- 否定命令だけに依存せず、望ましい状態を具体的に書く
- 必要以上に長くしない（目安: 日本語200字以内）
- 「夜」「路地裏」「薄暗い」「一人きり」が同時に重なる背景にしない
  （人けのある描写を1つ加えるか、十分な明るさの描写にする）
- 英語プロンプトの主語に"a girl"を使わない（性別を明示しないか"a boy"を使う）
"""

_IMAGE_PRESENT_INSTRUCTION = (
    "- 添付された実際のスナップ写真を背景・場所の**最優先の情報源**として使うこと。\n"
    "  写真に写っている実際の場所・物・光の状態を具体的に記述する。\n"
    "  ruki_mindの「空間・背景の傾向」やシーンテンプレートの例文にある場所\n"
    "  （図書館・神社参道・夜の繁華街の路地 等）は、写真の内容と矛盾する場合は使わない。\n"
    "  それらはあくまで写真が無い場合の補完用であり、デフォルトの選択肢ではない。"
)
_IMAGE_ABSENT_INSTRUCTION = (
    "- 場所・背景がシーン記述に含まれないため、人格プロファイルの「空間・背景の傾向」から選ぶ。"
)
_CHARACTER_SWAP_INSTRUCTION = (
    "- 添付された1枚目の写真は**元々ルキルキ以外のキャラクターが写っている**シーンである。\n"
    "  この写真は背景・場所・光・時間帯・周囲の状況の情報源としてのみ使うこと。\n"
    "  **写真に写っている人物の顔・髪型・体型・衣装は完全に無視し、一切描写に含めないこと。**\n"
    "  人物の外見は、必ず2枚目に添付されたルキルキ本人の外見リファレンス画像と、"
    "上記の人格プロファイル・固定情報の記述に従うこと。\n"
    "  つまり「元のキャラクターがいた場所に、代わりにルキルキが立っている」という"
    "シーンを描写すること。"
)


def _parse_llm_output(raw_text: str) -> tuple[str, str]:
    """LLM出力から日本語プロンプトと英語プロンプトを分離する。"""
    ja_match = re.search(r"##\s*日本語プロンプト\s*\n(.+?)(?=##\s*English Prompt|\Z)", raw_text, re.S)
    en_match = re.search(r"##\s*English Prompt\s*\n(.+)", raw_text, re.S)
    prompt_ja = ja_match.group(1).strip() if ja_match else raw_text.strip()
    prompt_en = en_match.group(1).strip() if en_match else ""
    return prompt_ja, prompt_en


async def generate_video_prompt(
    scene_id: str,
    genre: str = "日常",
    feature_rukiruki: bool = False,
) -> dict:
    """
    scene_id を指定して、00_builder.mdのルールに沿ったプロンプトを生成し、
    video_prompts テーブルへ保存する。戻り値は保存したレコード。

    scene_referencesにimage_url（実際のスナップ写真）がある場合は、
    それをLLMへ画像として渡し、背景描写の最優先の情報源として使わせる。
    取得できない場合はテキストのみのフロー（ruki_mindの傾向から推測）にフォールバックする。

    feature_rukiruki:
        Falseの場合、scene_referencesのmember_namesにRUKIRUKIが含まれるかで自動判定する。
        Trueを明示すると、たとえ写真が別キャラ（DrOhma等）のシーンであっても、
        その状況・背景だけを流用し、必ずルキルキ本人が写っているていで描写する
        （＝「あのシチュエーション、ルキルキも経験させたい」というユースケース向け）。
        この場合、ルキルキ本人の外見リファレンス画像も合わせてLLMへ渡し、
        写真に写っている元のキャラクターの外見は無視させる。
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
    image_url = scene.get("image_url")
    image_data_url = await _fetch_image_as_data_url(image_url) if image_url else None

    member_names = scene.get("member_names") or []
    scene_is_rukiruki = "RUKIRUKI" in member_names
    # 明示的にfeature_rukiruki=Trueが渡された場合、写真の主役がルキルキかどうかに
    # かかわらずルキルキを主演させる（＝他キャラのシチュエーションの流用）。
    do_character_swap = image_data_url is not None and not scene_is_rukiruki

    ruki_ref_data_url = None
    if do_character_swap or feature_rukiruki:
        ruki_ref_data_url = _load_ruki_reference_image_as_data_url()
        # 参照画像が用意できなければスワップ指示だけ出しても外見の担保にならないため、
        # 通常の画像指示にフォールバックする（人格プロファイルの文章記述のみに頼る）。
        do_character_swap = do_character_swap and ruki_ref_data_url is not None

    if do_character_swap:
        image_instruction = _CHARACTER_SWAP_INSTRUCTION
    elif image_data_url:
        image_instruction = _IMAGE_PRESENT_INSTRUCTION
    else:
        image_instruction = _IMAGE_ABSENT_INSTRUCTION

    prompt_text = _GENERATION_SYSTEM_PROMPT.format(
        mind_profile=mind_profile or "（プロファイル未生成）",
        builder_rules=builder_rules,
        pose_text=pose_text,
        genre=genre,
        image_instruction=image_instruction,
    )

    if do_character_swap:
        message = HumanMessage(content=[
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": image_data_url},
            {"type": "image_url", "image_url": ruki_ref_data_url},
        ])
        response = await llm_fast.ainvoke([message])
    elif image_data_url:
        message = HumanMessage(content=[
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": image_data_url},
        ])
        response = await llm_fast.ainvoke([message])
    else:
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
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/video_prompts"
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
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/video_prompts"
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
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/video_prompts"
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


# ═══════════════════════════════════════════════════════
# 会話中の質問応答用Tool（get_book_history と同型）
# ═══════════════════════════════════════════════════════

_STATUS_LABEL = {
    "generated": "生成のみ",
    "tested": "検証済み",
    "adopted": "採用",
    "rejected": "却下",
}


def _format_video_prompt_line(row: dict) -> str:
    date_str = (row.get("created_at") or "")[:10]
    genre = row.get("genre") or ""
    status_label = _STATUS_LABEL.get(row.get("status"), row.get("status") or "")
    model = row.get("tested_model")
    model_part = f"・{model}で検証" if model else ""
    excerpt = (row.get("prompt_ja") or "")[:40]
    line = f"- {date_str} [{genre}/{status_label}]{model_part} {excerpt}…"
    # 生URLをそのままLLMに渡すと、モデル自身のURL出力抑制学習によりタグ化を
    # 拒まれる傾向が実機で確認された（2026-07-14）。そのため生URLではなく
    # 短いIDだけを渡し、実際のURL解決はPython側（get_video_url_by_id）で行う。
    if row.get("result_video_url"):
        line += f"\n  動画ID: {row.get('id')}"
    return line


async def get_video_url_by_id(prompt_id: str) -> str | None:
    """
    video_promptsのidから実際のresult_video_urlを取得する。
    ||SHOW_VIDEO:id|| タグの解決に使う（nodes.py側から呼ばれる）。
    """
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/video_prompts"
    params = {"id": f"eq.{prompt_id}", "select": "result_video_url"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    if rows and rows[0].get("result_video_url"):
        return rows[0]["result_video_url"]
    return None


async def _find_video_prompts_by_keyword(query: str, limit: int = 5) -> list[dict]:
    """
    日本語プロンプト本文の部分一致（ilike）でvideo_promptsを検索する。
    「あの祭りの動画のプロンプトどんなだった？」のように内容を指す質問に使う。
    """
    base_url, _ = _sb()
    url = f"{base_url}/rest/v1/video_prompts"
    encoded_query = query.replace(" ", "%20")
    params = {
        "select": "*",
        "prompt_ja": f"ilike.*{encoded_query}*",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_pb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()


@tool
async def get_video_prompt_memories(query: str = "") -> str:
    """
    ユーザーから過去に作った動画生成プロンプトについて聞かれたときに呼ぶツール。
    「あの祭りの動画のプロンプトどんなだった？」のように特定のシーンを指す
    キーワードがあればqueryに入れて呼ぶ（日本語プロンプト本文に対する部分一致で検索する）。
    「今まで採用したプロンプトある？」「最近作った動画プロンプト教えて」のような
    一覧・確認系の質問にはqueryを空のまま呼ぶ（採用済みのものを新しい順で返す）。
    該当する記録が無い場合は「記録にはないみたい」という旨の文字列を返す。
    """
    if query.strip():
        rows = await _find_video_prompts_by_keyword(query.strip())
        if not rows:
            print(f"[DEBUG get_video_prompt_memories] query='{query}' ヒット0件")
            return f"「{query}」に該当する動画プロンプトの記録は見当たりません。"
        lines = [_format_video_prompt_line(row) for row in rows[:5]]
        result_text = "見つかった動画プロンプトの記録:\n" + "\n".join(lines)
        print(f"[DEBUG get_video_prompt_memories] query='{query}'\n{result_text}")
        return result_text

    rows = await list_video_prompts(status="adopted", limit=5)
    if not rows:
        print("[DEBUG get_video_prompt_memories] query='' adopted 0件")
        return "まだ採用済みの動画プロンプトはありません。"
    lines = [_format_video_prompt_line(row) for row in rows]
    result_text = "採用済みの動画プロンプト:\n" + "\n".join(lines)
    print(f"[DEBUG get_video_prompt_memories] query=''\n{result_text}")
    return result_text
