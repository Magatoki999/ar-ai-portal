# services/reminders.py
# ─────────────────────────────────────────────────────────────────────────────
# 軽量リマインダー（タスク）機能。
# Googleカレンダーに乗せるほどではない「レポート提出」「ゼミ発表準備」レベルの
# 単発タスクを、会話の中で登録・確認できるようにする。
#
# services/calendar.py（Googleカレンダーの実際の予定）とは別データソース。
# 「予定」ではなく「期限のあるやること」を扱う。
#
# 前提となるSupabaseテーブル（未作成の場合はSQL Editorで実行）:
#
#   create table reminders (
#     id bigint generated always as identity primary key,
#     title text not null,
#     due_at timestamptz not null,
#     is_done boolean not null default false,
#     notified_at timestamptz,
#     created_at timestamptz not null default now()
#   );
#
# Supabaseへのアクセスは services/memory.py と同じ作法（httpxでPostgRESTを直接叩く）に揃えている。
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from langchain_core.tools import tool

from services.memory import _sb, _sb_headers  # 既存の接続情報ヘルパーを再利用

JST = timezone(timedelta(hours=+9))


# ═══════════════════════════════════════════════════════
# 登録・取得・完了
# ═══════════════════════════════════════════════════════

async def save_reminder(title: str, due_at: str) -> bool:
    """
    リマインダーを1件保存する。
    due_at は ISO 8601 形式（例: "2026-08-01T23:59:00+09:00" または "2026-08-01"）を想定。
    タイムゾーン無しの日付だけが渡された場合はJST 23:59:00として扱う。
    """
    url, key = _sb()
    if not url or not key:
        return False

    title = title.strip()
    if not title:
        return False

    due_iso = _normalize_due_at(due_at)
    if not due_iso:
        print(f"[リマインダー] due_atの解析に失敗しました: {due_at!r}")
        return False

    endpoint = f"{url}/rest/v1/reminders"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    data = {
        "title": title,
        "due_at": due_iso,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[リマインダー] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[リマインダー] 登録しました: {title[:30]} (期限: {due_iso})")
        return True
    except Exception as e:
        print(f"[リマインダーエラー] {e}")
        return False


def _normalize_due_at(due_at: str) -> str | None:
    """
    "2026-08-01" のような日付のみの入力を、JST 23:59:00 のタイムゾーン付きISO文字列に補完する。
    既にタイムゾーン・時刻を含む場合はそのまま解釈して返す。
    """
    due_at = due_at.strip()
    if not due_at:
        return None
    try:
        # 日付のみ（"YYYY-MM-DD"）の場合、datetime.fromisoformatは時刻無しでパースできる
        dt = datetime.fromisoformat(due_at)
        if dt.tzinfo is None:
            # 時刻情報が無い（日付のみ）入力は、その日の終わりを期限とみなす
            if len(due_at) <= 10:
                dt = dt.replace(hour=23, minute=59, second=0)
            dt = dt.replace(tzinfo=JST)
        return dt.isoformat()
    except ValueError:
        return None


async def get_upcoming_reminders(limit: int = 10, include_done: bool = False) -> list:
    """未完了（またはinclude_done=Trueなら全件）のリマインダーを期限が近い順に取得する。"""
    url, key = _sb()
    if not url or not key:
        return []

    filters = "" if include_done else "&is_done=eq.false"
    endpoint = (
        f"{url}/rest/v1/reminders"
        f"?order=due_at.asc&limit={limit}{filters}"
        f"&select=id,title,due_at,is_done,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[リマインダー取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[リマインダー取得エラー] {e}")
        return []


async def find_reminders_by_title(query: str, limit: int = 5) -> list:
    """タイトルの部分一致（ilike）でリマインダーを検索する（完了済みも対象に含む）。"""
    url, key = _sb()
    if not url or not key:
        return []

    encoded_query = quote(f"*{query}*", safe="*")
    endpoint = (
        f"{url}/rest/v1/reminders"
        f"?title=ilike.{encoded_query}"
        f"&order=due_at.asc&limit={limit}"
        f"&select=id,title,due_at,is_done,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[リマインダー検索エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[リマインダー検索エラー] {e}")
        return []


async def mark_reminder_done(title: str) -> str | None:
    """
    タイトル部分一致で最も期限が近い未完了リマインダーを1件だけ完了にする。
    完了にしたリマインダーのタイトルを返す（見つからなければNone）。
    """
    url, key = _sb()
    if not url or not key:
        return None

    matches = await find_reminders_by_title(title, limit=5)
    matches = [m for m in matches if not m.get("is_done")]
    if not matches:
        return None

    target = matches[0]
    endpoint = f"{url}/rest/v1/reminders?id=eq.{target['id']}"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.patch(endpoint, json={"is_done": True}, headers=headers, timeout=5.0)
        if res.status_code not in (200, 204):
            print(f"[リマインダー完了] 更新失敗: status={res.status_code} body={res.text[:200]}")
            return None
        print(f"[リマインダー完了] 完了にしました: {target['title'][:30]}")
        return target["title"]
    except Exception as e:
        print(f"[リマインダー完了エラー] {e}")
        return None


# ═══════════════════════════════════════════════════════
# 自発通知ジョブ用（scheduler.py から呼ばれる）
# ═══════════════════════════════════════════════════════

async def get_due_soon_unnotified(hours_ahead: int = 24) -> list:
    """
    期限が hours_ahead 時間以内に迫っていて、まだ一度も通知していない未完了リマインダーを取得する。
    calendar_prep_job と違い「6時間おきに再チェック」ではなく「一生に一度だけ通知」の設計
    （notified_at が一度立てば二度と対象にならない）。
    """
    url, key = _sb()
    if not url or not key:
        return []

    now_utc = datetime.now(timezone.utc)
    deadline_utc = (now_utc + timedelta(hours=hours_ahead)).isoformat()

    endpoint = (
        f"{url}/rest/v1/reminders"
        f"?is_done=eq.false&notified_at=is.null"
        f"&due_at=lte.{deadline_utc}"
        f"&order=due_at.asc&limit=5"
        f"&select=id,title,due_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[リマインダー通知判定エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[リマインダー通知判定エラー] {e}")
        return []


async def mark_reminder_notified(reminder_id) -> None:
    """通知済みフラグ（notified_at）を現在時刻で立てる。二重通知を防ぐため。"""
    url, key = _sb()
    if not url or not key:
        return

    endpoint = f"{url}/rest/v1/reminders?id=eq.{reminder_id}"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    data = {"notified_at": datetime.now(timezone.utc).isoformat()}
    try:
        async with httpx.AsyncClient() as client:
            await client.patch(endpoint, json=data, headers=headers, timeout=5.0)
    except Exception as e:
        print(f"[リマインダー通知フラグ更新エラー] {e}")


# ═══════════════════════════════════════════════════════
# 会話中の質問応答・登録用Tool（get_book_history / get_my_schedule と同型）
# ═══════════════════════════════════════════════════════

@tool
async def set_reminder(title: str, due_at: str) -> str:
    """まがときさんが「〇〇を△△までにリマインドして」「〇〇の締切、覚えておいて」のように、
    レポート提出・ゼミ発表・買い物などの単発タスクの期限を覚えておいてほしいと頼んできたときに呼ぶツールです。
    Googleカレンダーの実際の予定（get_my_scheduleの対象）とは別物として扱ってください。

    Args:
        title: タスクの内容（例:「レポート提出」「ゼミ発表の準備」）。まがときさんの言葉をそのまま使ってよい。
        due_at: 期限を "YYYY-MM-DD" または "YYYY-MM-DDTHH:MM:00+09:00" のISO形式に変換して渡してください。
                システムプロンプトの現在の観測日時を基準に、"明日"「来週の金曜日」のような相対表現を
                具体的な日付に変換してください。時刻の指定が無い場合は日付のみで構いません。
    """
    ok = await save_reminder(title, due_at)
    if not ok:
        return "リマインダーの登録に失敗しました。日付をもう少し具体的に言ってもらえますか？"
    return f"「{title}」を登録しました。"


@tool
async def get_my_reminders(query: str = "") -> str:
    """まがときさんから「リマインダー一覧見せて」「他にやることある？」「〇〇っていつまでだっけ」のように、
    登録済みのリマインダー（タスク）について聞かれたときに呼ぶツールです。

    Args:
        query: 特定のタスク名について聞かれた場合はそのキーワードを入れてください。
               「他に何かある？」のような一覧・確認系の質問には空のまま呼んでください。
    """
    if query.strip():
        matches = await find_reminders_by_title(query.strip())
        if not matches:
            return f"「{query}」に該当するリマインダーは見当たりません。"
        return "見つかったリマインダー:\n" + "\n".join(_format_reminder_line(r) for r in matches[:5])

    upcoming = await get_upcoming_reminders(limit=5)
    if not upcoming:
        return "今のところ登録されているリマインダーはありません。"
    return "登録中のリマインダー:\n" + "\n".join(_format_reminder_line(r) for r in upcoming)


@tool
async def complete_reminder(title: str) -> str:
    """まがときさんが「〇〇終わった」「〇〇提出したよ」のように、登録済みのタスクを完了したと
    報告してきたときに呼ぶツールです。

    Args:
        title: 完了したタスクの内容や、分かる範囲のキーワード。
    """
    done_title = await mark_reminder_done(title)
    if not done_title:
        return f"「{title}」に該当する未完了のリマインダーは見当たりませんでした。"
    return f"「{done_title}」を完了にしました。"


def _format_reminder_line(r: dict) -> str:
    try:
        dt = datetime.fromisoformat(r["due_at"]).astimezone(JST)
        due_str = dt.strftime("%m/%d %H:%M")
    except (KeyError, ValueError, TypeError):
        due_str = r.get("due_at", "")
    return f"- {due_str} {r.get('title', '')}"
