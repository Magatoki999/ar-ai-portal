# services/health.py
# ─────────────────────────────────────────────────────────────────────────────
# ヘルスケア連携（歩数）サービス。
#
# 本アプリ（Webアプリ）からはHealthKitに直接アクセスできないため、iOSの
# 「ショートカット」アプリで組む個人用オートメーション（毎日0時に前日の歩数を
# 集計してPOST）を受け口として、main.pyの /api/health_update から書き込む。
#
# 前提となるSupabaseテーブル（未作成の場合はSQL Editorで実行）:
#
#   create table daily_health_logs (
#     date date primary key,
#     steps integer not null,
#     created_at timestamptz not null default now()
#   );
#
# Supabaseへのアクセスは services/memory.py と同じ作法（httpxでPostgRESTを直接叩く）。
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool

from services.memory import _sb, _sb_headers  # 既存の接続情報ヘルパーを再利用

JST = timezone(timedelta(hours=+9))


# ═══════════════════════════════════════════════════════
# 保存・取得
# ═══════════════════════════════════════════════════════

async def save_daily_steps(date: str, steps: int) -> bool:
    """
    1日分の歩数をupsertする。date は "YYYY-MM-DD" 形式。
    同じ日付で複数回送られてきても上書きされるだけなので、
    iOSショートカット側の多重実行を気にしなくてよい。
    """
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/daily_health_logs"
    headers  = {**_sb_headers(), "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    payload  = {"date": date, "steps": steps}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=payload, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[歩数] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[歩数] 保存しました: {date} → {steps}歩")
        return True
    except Exception as e:
        print(f"[歩数エラー] {e}")
        return False


async def get_recent_steps(days: int = 7) -> list[dict]:
    """直近days日分の歩数ログを日付が新しい順に取得する。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/daily_health_logs"
        f"?order=date.desc&limit={days}&select=date,steps"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[歩数取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[歩数取得エラー] {e}")
        return []


async def get_steps_for_date(date: str) -> int | None:
    """特定の日付（YYYY-MM-DD）の歩数を1件だけ取得する。無ければNone。"""
    url, key = _sb()
    if not url or not key:
        return None

    endpoint = f"{url}/rest/v1/daily_health_logs?date=eq.{date}&select=steps&limit=1"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            return None
        rows = res.json()
        return rows[0]["steps"] if rows else None
    except Exception as e:
        print(f"[歩数取得エラー] {e}")
        return None


# ═══════════════════════════════════════════════════════
# 会話文脈への自動反映（build_meal_context と同型）
# ═══════════════════════════════════════════════════════

def build_step_context(logs: list[dict]) -> str:
    """
    get_recent_steps() の結果を、synthesizer_node のプロンプトに直接埋め込める
    短いテキストブロックに整形する。0件の場合は空文字を返す
    （プロンプトに何も追加されない。build_meal_context と同じ設計）。
    """
    if not logs:
        return ""

    lines = []
    for log in logs[:7]:
        try:
            d = datetime.fromisoformat(log["date"]).strftime("%m/%d")
        except (KeyError, ValueError, TypeError):
            d = log.get("date", "")
        lines.append(f"{d}: {log.get('steps', 0):,}歩")

    return (
        "\n【最近の歩数記録】\n"
        + "\n".join(lines)
        + "\n（聞かれてもいないのに毎回歩数の話をする必要はありません。"
        "会話の流れで自然に触れられそうな時だけ、さりげなく使ってください）\n"
    )


# ═══════════════════════════════════════════════════════
# 会話中の質問応答用Tool（get_my_reminders と同型）
# ═══════════════════════════════════════════════════════

@tool
async def get_step_history(date: str = "") -> str:
    """まがときさんから「昨日何歩あるいた？」「最近の歩数どう？」「今週よく歩いてる？」のように、
    歩数について尋ねられたときに呼ぶツールです。

    Args:
        date: 特定の日について聞かれた場合は "YYYY-MM-DD" 形式に変換して渡してください。
              システムプロンプトの現在の観測日時を基準に、"昨日"「一昨日」のような
              相対表現を具体的な日付に変換してください。
              「最近」「今週」のような期間についての質問には空のまま呼んでください。
    """
    if date.strip():
        steps = await get_steps_for_date(date.strip())
        if steps is None:
            return f"{date}の歩数記録は見当たりませんでした。"
        return f"{date}は{steps:,}歩でした。"

    logs = await get_recent_steps(days=7)
    if not logs:
        return "まだ歩数の記録がありません。"

    lines = []
    for log in logs:
        try:
            d = datetime.fromisoformat(log["date"]).strftime("%m/%d")
        except (KeyError, ValueError, TypeError):
            d = log.get("date", "")
        lines.append(f"- {d}: {log.get('steps', 0):,}歩")

    return "直近の歩数記録:\n" + "\n".join(lines)
