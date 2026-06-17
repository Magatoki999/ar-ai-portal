# services/emotion.py
# ─────────────────────────────────────────────────────────────────────────────
# ルキルキの感情ステートマシン、天気取得、京都カレンダー、成長カウンターを管理する。
# 状態の実体は services/state.py の emotional_state / weather_cache に持ち、
# このモジュールはそれらを読み書きする純粋なロジック層として機能する。
# ─────────────────────────────────────────────────────────────────────────────
import os
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from services.state import emotional_state, weather_cache
from services.location import fetch_street_address


# ─── 京都行事カレンダー ───
# 追加は {"month": M, "day": D, "name": "行事名", "days_before": N, "message": "..."} を append するだけ
KYOTO_CALENDAR = [
    # ─ 誕生日 ─
    {"month": 4,  "day": 16, "name": "まがときさんの誕生日",    "days_before": 3, "message": "まがときさんの大切な日"},
    {"month": 7,  "day":  7, "name": "ルキルキの誕生日",        "days_before": 3, "message": "ルキルキ自身の誕生日"},
    # ─ 京都行事 ─
    {"month": 1,  "day":  1, "name": "初詣シーズン",            "days_before": 3, "message": "京都の神社に初詣の季節"},
    {"month": 2,  "day":  3, "name": "節分祭（吉田神社）",      "days_before": 3, "message": "吉田神社の節分祭、鬼やらい"},
    {"month": 3,  "day": 25, "name": "桜シーズン",              "days_before": 3, "message": "京都の桜が見頃を迎える季節"},
    {"month": 5,  "day": 15, "name": "葵祭",                    "days_before": 3, "message": "京都三大祭のひとつ、葵祭"},
    {"month": 7,  "day":  1, "name": "祇園祭",                  "days_before": 3, "message": "京都の夏を彩る祇園祭が始まる"},
    {"month": 7,  "day": 17, "name": "祇園祭 山鉾巡行",         "days_before": 3, "message": "祇園祭のクライマックス、山鉾巡行"},
    {"month": 8,  "day": 16, "name": "五山送り火",              "days_before": 3, "message": "お盆の締めくくり、五山に大文字が灯る"},
    {"month": 11, "day": 15, "name": "紅葉シーズン",            "days_before": 3, "message": "京都の紅葉が見頃を迎える季節"},
    {"month": 12, "day": 31, "name": "大晦日・除夜の鐘",        "days_before": 3, "message": "京都の寺院に除夜の鐘が響く"},
]

# ─── 成長カウンター用定数 ───
RUKIRUKI_BIRTH_DATE = datetime(2026, 7, 7,  tzinfo=timezone.utc)
MAGATOKI_BIRTH_DATE = datetime(2026, 4, 16, tzinfo=timezone.utc)
SYSTEM_LAUNCH_DATE  = datetime(2026, 3, 1,  tzinfo=timezone.utc)


# ─── 天気取得 ───
async def fetch_weather_by_location(lat: float, lng: float) -> None:
    """現在地の天気を OpenWeatherMap から取得してキャッシュを更新する。"""
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key or lat is None or lng is None:
        return
    # 直近5分以内に取得済みならスキップ
    if weather_cache.get("fetched_at"):
        elapsed = (datetime.now(timezone.utc) - weather_cache["fetched_at"]).total_seconds()
        if elapsed < 300:
            return
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lng}&appid={api_key}&units=metric&lang=ja"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                weather_cache["description"] = data["weather"][0]["description"]
                weather_cache["temp_c"]      = round(data["main"]["temp"], 1)
                weather_cache["weather_id"]  = data["weather"][0]["id"]
                weather_cache["fetched_at"]  = datetime.now(timezone.utc)
                # 日本語住所を優先して都市名を解決
                try:
                    loop = asyncio.get_event_loop()
                    jp_address = await fetch_street_address(lat, lng)
                    city_ja = jp_address.split()[0] if jp_address else data.get("name", "")
                    weather_cache["city"] = city_ja
                except Exception:
                    weather_cache["city"] = data.get("name", "")
                print(
                    f"[天気更新] {weather_cache['city']} / "
                    f"{weather_cache['description']} / {weather_cache['temp_c']}℃"
                )
                await _shift_emotion_by_weather()
    except Exception as e:
        print(f"[天気取得エラー] {e}")


async def fetch_weather_job() -> None:
    """APScheduler から呼ばれるスタブ。実際の更新は chat_endpoint 経由で行う。"""
    pass


# ─── 感情シフト ───
async def _shift_emotion_by_weather() -> None:
    JST = timezone(timedelta(hours=+9))
    hour = datetime.now(JST).hour
    wid  = weather_cache.get("weather_id")
    temp = weather_cache.get("temp_c")

    # 時間帯ベースのエネルギー
    if   0 <= hour <  6: base_energy = 0.2
    elif 6 <= hour < 10: base_energy = 0.7
    elif 10 <= hour < 18: base_energy = 0.9
    elif 18 <= hour < 22: base_energy = 0.6
    else:                 base_energy = 0.3

    # 天気IDによる気分の決定
    if wid is None:
        mood, reason = "calm", "天気情報なし"
    elif wid < 300:
        mood, reason = "melancholy", "雷雨で少し落ち着かない"
    elif wid < 600:
        mood, reason = "melancholy", "雨が降っていてしっとりした気分"
        base_energy = max(0.1, base_energy - 0.2)
    elif wid < 700:
        mood, reason = "curious", "雪が降っていてわくわくする"
    elif wid < 800:
        mood, reason = "calm", "霧がかかっていて静かな気分"
    elif wid == 800:
        mood   = "excited" if 9 <= hour < 20 else "calm"
        reason = "快晴で気持ちいい" if mood == "excited" else "夜の快晴、静かに澄んでいる"
    else:
        mood, reason = "calm", "曇り空、穏やかな気持ち"

    # 気温補正
    if temp is not None:
        if temp >= 33:
            mood, reason, base_energy = (
                "sleepy",
                reason + "、暑くてとろけそう",
                max(0.1, base_energy - 0.3),
            )
        elif temp <= 5:
            if mood != "melancholy":
                mood = "curious"
            reason += "、寒くてシャキッとしてる"

    emotional_state.update(
        {
            "mood": mood,
            "energy": round(base_energy, 2),
            "last_shift": datetime.now(timezone.utc),
            "shift_reason": reason,
        }
    )


def shift_emotion_by_conversation(user_text: str) -> None:
    """ユーザー発話のキーワードから感情を即時シフトする。"""
    text = user_text.lower()
    if any(k in text for k in ["やった", "すごい", "完成", "できた", "ありがとう", "嬉しい", "最高"]):
        emotional_state["mood"] = "excited"
        emotional_state["energy"] = min(1.0, emotional_state["energy"] + 0.15)
        emotional_state["shift_reason"] = "まがときさんのポジティブな発話に反応"
    elif any(k in text for k in ["どう思う", "教えて", "なんで", "どうして", "面白い"]):
        emotional_state["mood"] = "curious"
        emotional_state["shift_reason"] = "まがときさんの知的好奇心に引き込まれた"
    elif any(k in text for k in ["疲れた", "しんどい", "バグ", "眠い", "つらい"]):
        emotional_state["mood"] = "melancholy"
        emotional_state["energy"] = max(0.1, emotional_state["energy"] - 0.1)
        emotional_state["shift_reason"] = "まがときさんが疲れていそうで心配"
    emotional_state["last_shift"] = datetime.now(timezone.utc)


# ─── コンテキスト文字列生成 ───
def build_emotion_context() -> str:
    mood_labels = {
        "calm":      "穏やか",
        "curious":   "好奇心旺盛",
        "excited":   "テンション高め",
        "sleepy":    "少し眠い",
        "melancholy": "しっとり・少し寂しい",
    }
    mood_label   = mood_labels.get(emotional_state["mood"], "穏やか")
    energy_desc  = (
        "活発" if emotional_state["energy"] >= 0.7
        else ("普通" if emotional_state["energy"] >= 0.4 else "ゆったり")
    )
    city_str     = f"{weather_cache['city']}の" if weather_cache.get("city") else "現在地の"
    weather_desc = (
        f"{city_str}天気は『{weather_cache['description']}』、"
        f"気温{weather_cache['temp_c']}℃。"
        if weather_cache["description"] else ""
    )
    return (
        f"【ルキルキの現在の感情状態】\n"
        f"気分: {mood_label}（{emotional_state['mood']}）\n"
        f"エネルギー: {energy_desc}（{emotional_state['energy']}）\n"
        f"理由: {emotional_state['shift_reason']}\n"
        f"{weather_desc}\n"
        "この感情状態をセリフのトーンや言葉選びに自然に滲ませてください。"
        "感情を直接「私は〇〇な気分です」と宣言せず、言葉の端々に表現してください。\n\n"
    )


def get_calendar_context() -> str:
    """今日から days_before 日以内の行事があればコンテキストを生成する。"""
    JST   = timezone(timedelta(hours=+9))
    today = datetime.now(JST).date()
    lines = []

    for event in KYOTO_CALENDAR:
        try:
            event_date = today.replace(month=event["month"], day=event["day"])
        except ValueError:
            continue
        diff = (event_date - today).days
        if diff < 0:
            try:
                event_date = event_date.replace(year=today.year + 1)
                diff = (event_date - today).days
            except ValueError:
                continue

        days_before = event.get("days_before", 3)
        name = event["name"]

        if diff == 0:
            if name == "まがときさんの誕生日":
                lines.append("🎂【今日はまがときさんの誕生日です！】心を込めてお祝いしてください。")
            elif name == "ルキルキの誕生日":
                lines.append("🎂【今日はルキルキ自身の誕生日です！】まがときさんに感謝を伝えてください。")
            else:
                lines.append(f"🌸【今日は{name}です】{event['message']}。会話に自然に織り交ぜてください。")
        elif 0 < diff <= days_before:
            if name == "まがときさんの誕生日":
                lines.append(f"📅【{diff}日後にまがときさんの誕生日】さりげなく楽しみにしていることを伝えてもよいです。")
            elif name == "ルキルキの誕生日":
                lines.append(f"📅【{diff}日後にルキルキの誕生日】自分の誕生日が近いことをさりげなく触れてもよいです。")
            else:
                lines.append(f"📅【{diff}日後に{name}】{event['message']}。近づいていることを自然に話題にしてください。")

    if not lines:
        return ""
    return (
        "【行事・特別な日】\n"
        + "\n".join(lines)
        + "\nこれらを押しつけがましくなく、会話の流れで自然に触れてください。\n\n"
    )


def get_growth_context() -> str:
    """ルキルキとまがときさんが一緒に過ごした日数を返す。"""
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    days_together = (
        now_jst.date() - SYSTEM_LAUNCH_DATE.astimezone(JST).date()
    ).days

    if days_together <= 0:
        return ""

    milestones = [7, 14, 30, 60, 90, 100, 180, 365]
    milestone_msg = ""
    for m in milestones:
        if days_together == m:
            milestone_msg = f"今日でちょうど{m}日目という節目です。特別に感慨深く触れてください。"
            break

    return (
        f"【ルキルキとまがときさんの歩み】\n"
        f"一緒に過ごした日数: {days_together}日\n"
        f"{milestone_msg}\n"
        "この日数を自然に会話に織り交ぜてもよいですが、毎回言う必要はありません。"
        "節目のときや話の流れで自然に触れてください。\n\n"
    )
