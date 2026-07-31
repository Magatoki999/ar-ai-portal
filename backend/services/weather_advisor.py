# services/weather_advisor.py
# ─────────────────────────────────────────────────────────────────────────────
# 天気ベースの自発提案サービス。
#
# services/emotion.py の weather_cache は「今の天気」を感情シフトに使うだけで、
# 予報（これから）は扱っていない。こちらは OpenWeatherMap の5日間/3時間ごと
# 予報エンドポイントを使い、「これから傘が要りそうか」を見て、
# calendar_prep_job / reminder_prep_job と同じ「自発的に一言」の形で提案する。
#
# 環境変数: OPENWEATHERMAP_API_KEY （services/emotion.py と共用）
#
# app_state テーブルでの間隔制御は services/calendar.py の
# should_run_calendar_check / mark_calendar_checked と同じ作法（キー名だけ変える）。
# ─────────────────────────────────────────────────────────────────────────────
import os
from datetime import datetime, timedelta, timezone

import httpx

from services.memory import _sb, _sb_headers  # 既存の Supabase 接続ヘルパーを再利用
from services.state import weather_cache

JST = timezone(timedelta(hours=+9))

# OpenWeatherMapの天気コード（https://openweathermap.org/weather-conditions）のうち、
# 「傘があった方がいい」と言えるグループ。Thunderstorm(2xx) / Drizzle(3xx) / Rain(5xx) / Snow(6xx)。
_UMBRELLA_CODE_RANGES = [(200, 299), (300, 399), (500, 599), (600, 699)]

_LAST_CHECK_KEY = "weather_prep_last_checked_at"


def _needs_umbrella(weather_id: int) -> bool:
    return any(lo <= weather_id <= hi for lo, hi in _UMBRELLA_CODE_RANGES)


async def get_rain_forecast_message(hours_ahead: int = 30) -> str | None:
    """
    直近 hours_ahead 時間以内の3時間ごと予報を確認し、傘が要りそうなタイミングがあれば
    ルキルキ口調の一言提案を返す。不要・取得失敗時はNoneを返す。
    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    lat, lng = weather_cache.get("lat"), weather_cache.get("lng")
    if not api_key or lat is None or lng is None:
        # まだ一度も会話内で位置情報が来ておらず、最後に分かった座標が無い場合は何もしない
        return None

    url = (
        f"https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lng}&appid={api_key}&units=metric&lang=ja"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=8.0)
        if res.status_code != 200:
            print(f"[天気予報エラー] status={res.status_code} body={res.text[:150]}")
            return None
        data = res.json()
    except Exception as e:
        print(f"[天気予報エラー] {e}")
        return None

    now_utc = datetime.now(timezone.utc)
    deadline_utc = now_utc + timedelta(hours=hours_ahead)

    rain_slots = []
    for entry in data.get("list", []):
        try:
            slot_time = datetime.fromtimestamp(entry["dt"], tz=timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        if not (now_utc <= slot_time <= deadline_utc):
            continue
        weather_list = entry.get("weather") or []
        if not weather_list:
            continue
        weather_id = weather_list[0].get("id")
        if weather_id is not None and _needs_umbrella(weather_id):
            rain_slots.append(slot_time.astimezone(JST))

    if not rain_slots:
        return None

    earliest = min(rain_slots)
    now_jst = datetime.now(JST)
    if earliest.date() == now_jst.date():
        time_label = f"今日の{earliest.strftime('%H時')}頃"
    elif earliest.date() == (now_jst + timedelta(days=1)).date():
        time_label = f"明日の{earliest.strftime('%H時')}頃"
    else:
        time_label = earliest.strftime("%m/%d %H時頃")

    return f"{time_label}から雨みたいだよ。傘を持っていくの忘れないでね。"


# ═══════════════════════════════════════════════════════
# app_state テーブルを使った最終チェック日時の管理
# （services/calendar.py の should_run_calendar_check と同型。キーだけ分けて共存させる）
# ═══════════════════════════════════════════════════════

async def should_run_weather_check(min_interval_hours: int = 12) -> bool:
    """min_interval_hours 時間以上経過していれば True（初回はTrue）。"""
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/app_state?key=eq.{_LAST_CHECK_KEY}&select=value"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[天気先回り] app_state取得失敗: {res.status_code} {res.text[:150]}")
            return False

        rows = res.json()
        if not rows:
            return True

        last_checked_str = rows[0].get("value")
        if not last_checked_str:
            return True

        last_checked = datetime.fromisoformat(last_checked_str)
        elapsed_hours = (datetime.now(JST) - last_checked).total_seconds() / 3600
        return elapsed_hours >= min_interval_hours

    except Exception as e:
        print(f"[天気先回り] should_run_weather_check エラー: {e}")
        return False


async def mark_weather_checked() -> None:
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
            print(f"[天気先回り] app_state更新失敗: {res.status_code} {res.text[:150]}")
    except Exception as e:
        print(f"[天気先回り] mark_weather_checked エラー: {e}")
