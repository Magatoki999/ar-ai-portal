# services/calendar.py
# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar 連携サービス。
# リフレッシュトークンを使ってアクセストークンを都度再発行し、
# 直近の予定（デフォルトでは今から48時間以内）を取得する。
#
# 環境変数:
#   GOOGLE_CLIENT_ID
#   GOOGLE_CLIENT_SECRET
#   GOOGLE_REFRESH_TOKEN
#
# 用途:
#   scheduler.py の定期ジョブから呼ばれ、直近の予定をルキルキに
#   「気を配らせる」ための下準備として使う。
# ─────────────────────────────────────────────────────────────────────────────
import os
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool

from services.memory import _sb, _sb_headers  # 既存の Supabase 接続ヘルパーを再利用

JST = timezone(timedelta(hours=+9))

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)


async def _refresh_access_token() -> str | None:
    """
    リフレッシュトークンを使って、その場限りのアクセストークンを発行する。
    アクセストークンは短命（通常1時間）なので、毎回このリフレッシュを行う。
    失敗時は None を返す。
    """
    client_id     = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        print("[Calendar] GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN が未設定です")
        return None

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type":    "refresh_token",
                },
                timeout=10.0,
            )
        if res.status_code != 200:
            print(f"[Calendar] アクセストークン更新失敗: {res.status_code} {res.text[:200]}")
            return None
        return res.json().get("access_token")
    except Exception as e:
        print(f"[Calendar] アクセストークン更新エラー: {e}")
        return None


async def get_upcoming_events(hours_ahead: int = 48) -> list[dict]:
    """
    今から hours_ahead 時間以内に始まる予定を取得して返す。
    各要素は {"title": str, "start": datetime, "end": datetime,
              "location": str, "description": str} の形。
    取得失敗時は空リストを返す（呼び出し側でエラーハンドリング不要にするため）。
    """
    access_token = await _refresh_access_token()
    if not access_token:
        return []

    now      = datetime.now(JST)
    time_max = now + timedelta(hours=hours_ahead)

    params = {
        "timeMin":      now.isoformat(),
        "timeMax":      time_max.isoformat(),
        "singleEvents": "true",
        "orderBy":      "startTime",
        "maxResults":   20,
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                GOOGLE_CALENDAR_API,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                timeout=10.0,
            )
        if res.status_code != 200:
            print(f"[Calendar] 予定取得失敗: {res.status_code} {res.text[:200]}")
            return []

        items = res.json().get("items", [])
        events = []
        for item in items:
            start_raw = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end_raw   = item.get("end", {}).get("dateTime")   or item.get("end", {}).get("date")
            events.append({
                "title":       item.get("summary", "（無題の予定）"),
                "start":       start_raw,
                "end":         end_raw,
                "location":    item.get("location", ""),
                "description": item.get("description", ""),
            })

        print(f"[Calendar] 直近{hours_ahead}時間以内の予定を{len(events)}件取得しました")
        return events

    except Exception as e:
        print(f"[Calendar] 予定取得エラー: {e}")
        return []


# ─── 予定から「準備提案」を作る ───

PREP_SUGGESTION_PROMPT = (
    "あなたはXR観測ナビゲーター「ルキルキ」です。ユーザーの直近の予定を見て、"
    "事前に準備しておいた方がいいことがあれば、短く一言だけ提案してください。\n\n"
    "【判断ルール】\n"
    "- 提案する価値が本当にある場合のみ、JSON形式で {{\"should_notify\": true, \"message\": \"提案文\"}} "
    "を返してください。\n"
    "- 当たり前すぎる予定（ただの「会議」「打ち合わせ」など準備が思いつかないもの）には "
    "{{\"should_notify\": false, \"message\": \"\"}} を返してください。\n"
    "- 提案するのは、初めて行く場所っぽい／長時間（4時間以上）／"
    "セミナーや勉強会など持ち物が想像できるもの／資料や機材が必要そうなもの、"
    "に当てはまる場合だけにしてください。\n"
    "- message は1文、50文字以内、ルキルキらしい親しみのある口調にしてください。"
    "URLや箇条書きは禁止です。\n"
    "- 出力は厳密にJSONのみ。説明文や前置き、Markdownのコードブロックは付けないでください。\n\n"
    "【予定情報】\n"
    "タイトル: {title}\n"
    "場所: {location}\n"
    "開始: {start}\n"
    "終了: {end}\n"
    "メモ: {description}\n"
)


async def build_prep_suggestion(event: dict, llm) -> dict | None:
    """
    1件の予定について、LLMに「準備提案をすべきか」を判断させる。
    戻り値: {"should_notify": bool, "message": str} または None（判定失敗時）
    呼び出し側の llm は ChatOpenAI 等、.ainvoke(str) -> AIMessage を持つインスタンスを想定。
    """
    import json

    prompt = PREP_SUGGESTION_PROMPT.format(
        title=event.get("title", ""),
        location=event.get("location") or "（場所の記載なし）",
        start=event.get("start", ""),
        end=event.get("end", ""),
        description=event.get("description") or "（メモなし）",
    )

    try:
        response = await llm.ainvoke(prompt)
        raw = response.content.strip()

        # コードブロックで返ってきた場合の保険的な除去
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or "should_notify" not in parsed:
            print(f"[Calendar] 準備提案の形式が不正: {raw[:200]}")
            return None
        return parsed

    except json.JSONDecodeError:
        print(f"[Calendar] 準備提案のJSON解析に失敗: {raw[:200]}")
        return None
    except Exception as e:
        print(f"[Calendar] 準備提案の生成エラー: {e}")
        return None


async def find_past_episode_for_event(event: dict) -> dict | None:
    """
    予定のタイトルまたは場所が、過去のエピソードメモリと一致するか調べる。
    一致した場合は最新の1件を返す（「前回もこのイベント行きましたね」のような
    一言を作るための材料）。一致しなければ None。

    services.memory の Supabase 接続設定（_sb / _sb_headers）を再利用する。
    """
    url, key = _sb()
    if not url or not key:
        return None

    title    = (event.get("title") or "").strip()
    location = (event.get("location") or "").strip()
    keyword  = location or title
    if not keyword:
        return None

    pattern = f"*{keyword}*"
    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?or=(location_name.ilike.{pattern},user_message.ilike.{pattern})"
        f"&order=created_at.desc&limit=1&select=location_name,user_message,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code == 200:
                rows = res.json()
                if rows:
                    return rows[0]
    except Exception as e:
        print(f"[Calendar] 過去エピソード検索エラー: {e}")
    return None


# ─── app_state テーブル（汎用キーバリュー）を使った最終チェック日時の管理 ───
# Render無料プランはスリープするため、APSchedulerのcronに頼らず、
# 「アプリが開かれたタイミング」で最後にチェックした時刻を見て、
# 6時間以上空いていれば再チェックする方式にする。
_LAST_CHECK_KEY = "calendar_prep_last_checked_at"


async def should_run_calendar_check(min_interval_hours: int = 6) -> bool:
    """
    app_state テーブルの最終チェック日時を見て、min_interval_hours 時間以上
    経過していれば True を返す（再チェックしてよい）。
    レコードが無い場合（初回）は True を返す。
    """
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/app_state?key=eq.{_LAST_CHECK_KEY}&select=value"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[Calendar] app_state取得失敗: {res.status_code} {res.text[:150]}")
            return False

        rows = res.json()
        if not rows:
            return True  # 初回チェック

        last_checked_str = rows[0].get("value")
        if not last_checked_str:
            return True

        last_checked = datetime.fromisoformat(last_checked_str)
        elapsed_hours = (datetime.now(JST) - last_checked).total_seconds() / 3600
        return elapsed_hours >= min_interval_hours

    except Exception as e:
        print(f"[Calendar] should_run_calendar_check エラー: {e}")
        return False


async def mark_calendar_checked() -> None:
    """app_state テーブルに現在時刻（JST）を最終チェック日時として upsert する。"""
    url, key = _sb()
    if not url or not key:
        return

    endpoint = f"{url}/rest/v1/app_state"
    headers  = {**_sb_headers(), "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    payload  = {
        "key":   _LAST_CHECK_KEY,
        "value": datetime.now(JST).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, headers=headers, json=payload, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[Calendar] app_state更新失敗: {res.status_code} {res.text[:150]}")
    except Exception as e:
        print(f"[Calendar] mark_calendar_checked エラー: {e}")


# ─── 会話中にLLMが「予定は？」と聞かれたときだけ呼ぶTool ───
# calendar_prep_job（先回り提案）とは別経路。こちらはユーザーの質問に応答するためのもので、
# LLMが必要と判断したときだけ呼ばれるため、聞かれていないターンではAPIコストが発生しない。

@tool
async def get_my_schedule(hours_ahead: int = 48) -> str:
    """
    ユーザーから「今日の予定」「これからの予定」「カレンダー」について聞かれたときに呼ぶツール。
    Googleカレンダーから直近の予定を取得し、ルキルキが話せる短い文章にして返す。
    予定が無い場合は「予定はありません」という旨の文字列を返す。

    Args:
        hours_ahead: 何時間先までの予定を取得するか（デフォルト48時間 = 今日と明日）。
                      「今日」だけ聞かれた場合も無理に絞り込まず、デフォルトのままでよい。
    """
    events = await get_upcoming_events(hours_ahead=hours_ahead)
    if not events:
        return "直近の予定はありません。"

    lines = []
    for ev in events[:5]:  # 多すぎると喋りすぎになるので上限5件
        title = ev.get("title", "（無題の予定）")
        start = ev.get("start", "")
        # ISO形式の日時から "M/D H:MM" 程度の簡潔な表記に変換（失敗時はそのまま使う）
        try:
            dt = datetime.fromisoformat(start)
            start_str = dt.strftime("%m/%d %H:%M")
        except (ValueError, TypeError):
            start_str = start
        location = ev.get("location", "")
        line = f"{start_str} {title}"
        if location:
            line += f"（{location}）"
        lines.append(line)

    return "予定一覧：\n" + "\n".join(lines)
