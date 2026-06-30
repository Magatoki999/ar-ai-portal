# services/character_bible.py
# ─────────────────────────────────────────────────────────────────────────────
# 「ルキルキ マインドプロファイル」生成バッチ。
#
# 目的：ルキルキというキャラクターのアイデンティティを、将来AI動画生成や
# 他者の脚本へのキャスティング素材として使えるMarkdown（ruki_mind/YYYY-MM.md）
# として月次で蓄積する。版を残す方式（上書きしない）。
#
# データソース：
#   - episode_memories（エピソード記憶。GPS座標・画像付き）
#   - meal_logs（食事記録）
#   - reading_logs（読書記録）
#   - 会話ログ由来の感情・口調サンプル（今後 chat_logs 的なものがあれば拡張）
#
# 設計方針：
#   - ユーザーの行動データを「ルキルキ自身が経験したこと」として一人称的に
#     語り直す（MagatokiLabの方針：読んだ本・食べた食事・見た光景は
#     ルキルキとユーザーが共有する経験として扱う）。
#   - 生成は「Curator」という、ユーザーに見えない裏方専属のAIエージェントの
#     ペルソナ（persona/curator_persona.md）で行う。ルキルキ本体の会話エージェント
#     （agents/nodes.py の synthesizer_node 等）とは完全に独立しており、
#     ユーザーとの会話フローには一切影響しない。
#   - 既存の services/memory.py の関数（get_recent_episodes, get_recent_meal_logs等）は
#     会話プロンプト用に整形済みの文字列やlimit固定の設計になっているため、
#     ここでは月次の生データ取得用に独自のクエリ関数を持つ（既存関数は変更しない）。
#   - データが無い項目は「まだ観測されていない」と正直に書く（Curatorペルソナで明記）。
#     捏造はキャラクターIPの信頼性を損なうため、プロンプト側で厳に禁止している。
# ─────────────────────────────────────────────────────────────────────────────
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from openai import AsyncOpenAI

from services.memory import _sb, _sb_headers

# Curatorペルソナの配置場所。rukiruki_persona.md と同じ階層（backend/persona/）を想定。
_CURATOR_PERSONA_PATH = Path(__file__).resolve().parent.parent / "persona" / "curator_persona.md"

# マインドプロファイルの書き出し先。リポジトリルート直下（src/, backend/ と同階層）。
_RUKI_MIND_DIR = Path(__file__).resolve().parent.parent.parent / "ruki_mind"

_OPENAI_MODEL = os.getenv("CHARACTER_BIBLE_MODEL", "gpt-4o")


# ═══════════════════════════════════════════════════════
# データ収集（月次・生データ）
# ═══════════════════════════════════════════════════════

async def _fetch_month_episode_memories(days: int = 30) -> list:
    """直近N日分のエピソード記憶を生データのまま取得する（プロンプト整形前）。"""
    url, key = _sb()
    if not url or not key:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?created_at=gte.{quote(since, safe='')}"
        f"&order=created_at.desc&limit=200&select=*"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=8.0)
        if res.status_code != 200:
            print(f"[character_bible] エピソード記憶取得エラー: status={res.status_code}")
            return []
        return res.json()
    except Exception as e:
        print(f"[character_bible] エピソード記憶取得エラー: {e}")
        return []


async def _fetch_month_meal_logs(days: int = 30) -> list:
    """直近N日分の食事記録を取得する。"""
    url, key = _sb()
    if not url or not key:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    endpoint = (
        f"{url}/rest/v1/meal_logs"
        f"?created_at=gte.{quote(since, safe='')}"
        f"&order=created_at.desc&limit=200"
        f"&select=meal_type,description,is_alone,healthiness,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=8.0)
        if res.status_code != 200:
            print(f"[character_bible] 食事記録取得エラー: status={res.status_code}")
            return []
        return res.json()
    except Exception as e:
        print(f"[character_bible] 食事記録取得エラー: {e}")
        return []


async def _fetch_month_reading_logs(days: int = 30) -> list:
    """直近N日分の読書記録を取得する。"""
    url, key = _sb()
    if not url or not key:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    endpoint = (
        f"{url}/rest/v1/reading_logs"
        f"?created_at=gte.{quote(since, safe='')}"
        f"&order=created_at.desc&limit=200"
        f"&select=title,author,genre,series_title,borrow_count,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=8.0)
        if res.status_code != 200:
            print(f"[character_bible] 読書記録取得エラー: status={res.status_code}")
            return []
        return res.json()
    except Exception as e:
        print(f"[character_bible] 読書記録取得エラー: {e}")
        return []


# ═══════════════════════════════════════════════════════
# 前月分プロファイルの取得（変化の比較に使う）
# ═══════════════════════════════════════════════════════

def _previous_month_str(year: int, month: int) -> str:
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _load_previous_profile(year: int, month: int) -> str | None:
    """前月分のマインドプロファイルがあれば読み込む（Curatorへの比較材料として渡す）。"""
    prev = _previous_month_str(year, month)
    path = _RUKI_MIND_DIR / f"{prev}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ═══════════════════════════════════════════════════════
# データの一人称的な整形（MagatokiLabの「共有」方針の核）
# ═══════════════════════════════════════════════════════

def _format_shared_experiences(episodes: list, meals: list, books: list) -> str:
    """
    収集した生データを、Curatorへの入力用にテキストへ整形する。
    ここでは生データをそのまま渡し、「ルキルキ自身の経験として語り直す」変換は
    Curator（LLM側）に委ねる。Curatorペルソナの指示がこの変換を担う。
    """
    lines = []

    lines.append(
        "## 今月、私（ルキルキ）が経験したこと（エピソード記憶）\n"
        "※以下はあなた（ルキルキ）自身の体験記録です。「ユーザーが○○した」ではなく"
        "「私は○○した」という前提で読んでください。"
    )
    if episodes:
        for ep in episodes[:50]:
            summary = ep.get("summary", "")
            mood = ep.get("mood_at_time", "")
            created = ep.get("created_at", "")
            lines.append(f"- [{created}] 私は次のことを経験した: {summary}（その時の気分: {mood}）")
    else:
        lines.append("- （今月、私が経験したこととして記録されているものはありません）")

    lines.append(
        "\n## 今月、私（ルキルキ）が食べたもの（食事記録）\n"
        "※孤食ロボット機能により記録された、私自身の食事の記録として読んでください。"
    )
    if meals:
        for m in meals[:50]:
            lines.append(
                f"- [{m.get('created_at','')}] 私は{m.get('meal_type','')}にこれを食べた: "
                f"{m.get('description','')}（一人で食べたか: {m.get('is_alone')}）"
            )
    else:
        lines.append("- （今月、私が食べたものとして記録されているものはありません）")

    lines.append(
        "\n## 今月、私（ルキルキ）が読んだ本（読書通帳）\n"
        "※読書通帳機能により記録された、私自身が読んだ本の記録として読んでください。"
    )
    if books:
        for b in books[:50]:
            series = f"・シリーズ:{b['series_title']}" if b.get("series_title") else ""
            lines.append(
                f"- [{b.get('created_at','')}] 私はこの本を読んだ: {b.get('title','')}"
                f"（著者: {b.get('author','')}{series}）"
            )
    else:
        lines.append("- （今月、私が読んだ本として記録されているものはありません）")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Curatorによるマインドプロファイル生成
# ═══════════════════════════════════════════════════════

async def generate_mind_profile(year: int | None = None, month: int | None = None) -> str | None:
    """
    指定された年月（省略時は今月）のマインドプロファイルを生成し、
    ruki_mind/YYYY-MM.md として保存する。版を残す方式（既存ファイルは上書きしない）。
    生成したMarkdown文字列を返す。失敗した場合はNoneを返す。
    """
    now = datetime.now(timezone.utc)
    year = year or now.year
    month = month or now.month
    version_str = f"{year}-{month:02d}"

    if not _CURATOR_PERSONA_PATH.exists():
        print(f"[character_bible] Curatorペルソナファイルが見つかりません: {_CURATOR_PERSONA_PATH}")
        return None

    curator_system_prompt = _CURATOR_PERSONA_PATH.read_text(encoding="utf-8")

    episodes = await _fetch_month_episode_memories()
    meals = await _fetch_month_meal_logs()
    books = await _fetch_month_reading_logs()

    shared_experiences = _format_shared_experiences(episodes, meals, books)
    previous_profile = _load_previous_profile(year, month)

    user_prompt_parts = [
        f"対象期間: {version_str}（直近30日分のデータ）",
        shared_experiences,
    ]
    if previous_profile:
        user_prompt_parts.append(
            "\n## 前月のマインドプロファイル（変化の比較に使ってください）\n" + previous_profile
        )
    else:
        user_prompt_parts.append(
            "\n## 前月のマインドプロファイル\n（前月分は存在しません。今回が初回の生成です）"
        )

    user_prompt = "\n".join(user_prompt_parts)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[character_bible] OPENAI_API_KEYが設定されていません")
        return None

    client = AsyncOpenAI(api_key=api_key)
    try:
        response = await client.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": curator_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )
        markdown = response.choices[0].message.content
    except Exception as e:
        print(f"[character_bible] LLM呼び出しエラー: {e}")
        return None

    if not markdown:
        print("[character_bible] LLMから空の応答が返りました")
        return None

    saved = _save_profile(version_str, markdown)
    if not saved:
        return None

    print(f"[character_bible] マインドプロファイルを生成しました: ruki_mind/{version_str}.md")
    return markdown


def _save_profile(version_str: str, markdown: str) -> bool:
    """
    生成したマークダウンを ruki_mind/YYYY-MM.md に保存する。
    既に同名ファイルが存在する場合は上書きしない（版を残す方式の原則）。
    再生成したい場合は、呼び出し側が明示的に古いファイルを削除してから呼ぶこと。
    """
    try:
        _RUKI_MIND_DIR.mkdir(parents=True, exist_ok=True)
        path = _RUKI_MIND_DIR / f"{version_str}.md"
        if path.exists():
            print(f"[character_bible] 既に{version_str}.mdが存在するため上書きしません（版を残す方式）")
            return False
        path.write_text(markdown, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[character_bible] 保存エラー: {e}")
        return False


async def get_latest_mind_profile() -> str | None:
    """
    ruki_mind/ 内で最新のマインドプロファイルを読み込む。
    会話Tool等から「今のルキルキの人格」を参照したくなった場合に使える想定
    （現時点では未接続。将来の拡張ポイント）。
    """
    if not _RUKI_MIND_DIR.exists():
        return None
    files = sorted(_RUKI_MIND_DIR.glob("*.md"), reverse=True)
    if not files:
        return None
    return files[0].read_text(encoding="utf-8")
