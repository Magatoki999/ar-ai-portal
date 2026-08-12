# services/journey.py
# ─────────────────────────────────────────────────────────────────────────────
# 東海道五十三次 歩行シミュレーション。
# 毎日の歩数を距離に変換して積み上げ、京都・三条大橋を起点に江戸・日本橋を
# 目指す「旅」として扱う。宿場（チェックポイント）を新しく超えた瞬間だけ
# 自発的に報告する設計は spot_proximity_job と同じエッジトリガー方式。
#
# 前提となるSupabaseテーブル（未作成の場合はSQL Editorで実行）:
#
#   create table journey_progress (
#     id bigint primary key default 1,
#     total_distance_km numeric not null default 0,
#     last_notified_index integer not null default 0,
#     updated_at timestamptz not null default now()
#   );
#   insert into journey_progress (id, total_distance_km, last_notified_index)
#   values (1, 0, 0) on conflict (id) do nothing;
#
# 距離データについての注意：
#   宿場間の正確な実測距離を出典付きで全53区間分揃えるのは断念し、
#   総距離（約495km、「宿村大概帳」準拠）・判明している一部区間
#   （宮宿-桑名宿24.5km等）を踏まえた近似値にしている。正確な資料が
#   見つかれば KM_FROM_KYOTO の数値だけ差し替えれば良い設計にしてある。
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
import os

import httpx
from langchain_core.tools import tool

from services.memory import _sb, _sb_headers
from services.state import manager
from services.tts import generate_tts

DEFAULT_STRIDE_M = 0.7  # 1歩あたりの歩幅（m）。将来的にユーザー実測値に差し替え可能にする想定。

# 京都・三条大橋(0km)を起点に、江戸・日本橋（約495km）へ向かう順。
# 「宿場」は本来53だが、起点・終点（三条大橋・日本橋）を含めて55地点として管理する。
TOKAIDO_ROUTE: list[dict] = [
    {"name": "三条大橋",   "km": 0.0,   "note": "旅の出発点、京都"},
    {"name": "大津宿",     "km": 4.0,   "note": "東海道最後（京都から見れば最初）の宿場"},
    {"name": "草津宿",     "km": 16.0,  "note": "中山道との分岐点として栄えた宿場町"},
    {"name": "石部宿",     "km": 28.0,  "note": ""},
    {"name": "水口宿",     "km": 39.0,  "note": ""},
    {"name": "土山宿",     "km": 49.0,  "note": ""},
    {"name": "坂下宿",     "km": 58.0,  "note": "鈴鹿峠の手前"},
    {"name": "関宿",       "km": 64.0,  "note": "今も宿場町の町並みが残る"},
    {"name": "亀山宿",     "km": 70.0,  "note": ""},
    {"name": "庄野宿",     "km": 75.0,  "note": ""},
    {"name": "石薬師宿",   "km": 79.0,  "note": ""},
    {"name": "四日市宿",   "km": 87.0,  "note": ""},
    {"name": "桑名宿",     "km": 100.0, "note": "七里の渡しで宮宿と結ばれていた港町"},
    {"name": "宮宿",       "km": 124.5, "note": "東海道随一の宿場町、熱田"},
    {"name": "鳴海宿",     "km": 132.0, "note": ""},
    {"name": "池鯉鮒宿",   "km": 143.0, "note": "現在の知立"},
    {"name": "岡崎宿",     "km": 155.0, "note": ""},
    {"name": "藤川宿",     "km": 162.0, "note": ""},
    {"name": "赤坂宿",     "km": 169.0, "note": ""},
    {"name": "御油宿",     "km": 170.7, "note": "赤坂宿との間はわずか1.7km、東海道最短区間"},
    {"name": "吉田宿",     "km": 178.0, "note": "現在の豊橋"},
    {"name": "二川宿",     "km": 186.0, "note": ""},
    {"name": "白須賀宿",   "km": 195.0, "note": "潮見坂からの絶景で知られる"},
    {"name": "新居宿",     "km": 201.0, "note": "今切の渡し"},
    {"name": "舞坂宿",     "km": 206.0, "note": ""},
    {"name": "浜松宿",     "km": 216.0, "note": ""},
    {"name": "見付宿",     "km": 248.0, "note": "現在の磐田"},
    {"name": "袋井宿",     "km": 257.0, "note": ""},
    {"name": "掛川宿",     "km": 266.0, "note": ""},
    {"name": "日坂宿",     "km": 272.0, "note": "小夜の中山峠の手前"},
    {"name": "金谷宿",     "km": 281.0, "note": ""},
    {"name": "島田宿",     "km": 286.0, "note": "大井川の渡し"},
    {"name": "藤枝宿",     "km": 295.0, "note": ""},
    {"name": "岡部宿",     "km": 302.0, "note": ""},
    {"name": "丸子宿",     "km": 310.0, "note": "とろろ汁で有名"},
    {"name": "府中宿",     "km": 316.0, "note": "現在の静岡"},
    {"name": "江尻宿",     "km": 327.0, "note": "現在の清水"},
    {"name": "興津宿",     "km": 333.0, "note": ""},
    {"name": "由比宿",     "km": 339.0, "note": "薩埵峠の絶景"},
    {"name": "蒲原宿",     "km": 344.0, "note": ""},
    {"name": "吉原宿",     "km": 352.0, "note": ""},
    {"name": "原宿",       "km": 361.0, "note": "富士山の眺めで知られる"},
    {"name": "沼津宿",     "km": 368.0, "note": ""},
    {"name": "三島宿",     "km": 379.0, "note": "箱根越えの手前"},
    {"name": "箱根宿",     "km": 392.0, "note": "東海道最大の難所、箱根八里"},
    {"name": "小田原宿",   "km": 406.0, "note": ""},
    {"name": "大磯宿",     "km": 417.0, "note": ""},
    {"name": "平塚宿",     "km": 423.0, "note": ""},
    {"name": "藤沢宿",     "km": 430.0, "note": ""},
    {"name": "戸塚宿",     "km": 437.0, "note": ""},
    {"name": "保土ヶ谷宿", "km": 445.0, "note": ""},
    {"name": "神奈川宿",   "km": 452.0, "note": ""},
    {"name": "川崎宿",     "km": 461.0, "note": ""},
    {"name": "品川宿",     "km": 471.0, "note": ""},
    {"name": "日本橋",     "km": 495.0, "note": "東海道の終点、旅の目的地"},
]


def steps_to_km(steps: int, stride_m: float = DEFAULT_STRIDE_M) -> float:
    return round(steps * stride_m / 1000, 3)


async def get_journey_progress() -> dict:
    """{'total_distance_km': float, 'last_notified_index': int} を返す。取得失敗時はゼロ初期値。"""
    url, key = _sb()
    if not url or not key:
        return {"total_distance_km": 0.0, "last_notified_index": 0}

    endpoint = f"{url}/rest/v1/journey_progress?id=eq.1&select=total_distance_km,last_notified_index"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            return {"total_distance_km": 0.0, "last_notified_index": 0}
        rows = res.json()
        if not rows:
            return {"total_distance_km": 0.0, "last_notified_index": 0}
        return {
            "total_distance_km": float(rows[0].get("total_distance_km", 0.0)),
            "last_notified_index": int(rows[0].get("last_notified_index", 0)),
        }
    except Exception as e:
        print(f"[旅] 進捗取得エラー: {e}")
        return {"total_distance_km": 0.0, "last_notified_index": 0}


async def save_journey_progress(total_distance_km: float, last_notified_index: int) -> bool:
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/journey_progress"
    headers  = {**_sb_headers(), "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    payload  = {
        "id": 1,
        "total_distance_km": total_distance_km,
        "last_notified_index": last_notified_index,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=payload, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[旅] 進捗保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[旅] 進捗保存エラー: {e}")
        return False


def current_position(total_distance_km: float) -> dict:
    """
    現在の累積距離から、直近に通過した地点と次の目的地を割り出す。
    戻り値: {"last_station": dict, "next_station": dict|None, "progress_ratio": float}
    """
    last_station = TOKAIDO_ROUTE[0]
    next_station = TOKAIDO_ROUTE[1] if len(TOKAIDO_ROUTE) > 1 else None
    for i, station in enumerate(TOKAIDO_ROUTE):
        if station["km"] <= total_distance_km:
            last_station = station
            next_station = TOKAIDO_ROUTE[i + 1] if i + 1 < len(TOKAIDO_ROUTE) else None
        else:
            break
    total_route_km = TOKAIDO_ROUTE[-1]["km"]
    return {
        "last_station": last_station,
        "next_station": next_station,
        "progress_ratio": min(total_distance_km / total_route_km, 1.0),
    }


async def apply_daily_steps(steps: int, stride_m: float = DEFAULT_STRIDE_M) -> dict | None:
    """
    その日の歩数を旅の進捗に反映する。services/health.py の save_daily_steps 成功時に
    呼ばれる想定。新しく宿場を1つ以上通過していれば、その通過情報（一番新しいもの）を
    返す。通過が無ければNoneを返す。
    """
    progress = await get_journey_progress()
    old_km = progress["total_distance_km"]
    new_km = old_km + steps_to_km(steps, stride_m)

    # 新しく通過した地点（index）を探す。複数まとめて通過した場合は最新のものだけ通知する
    # （一気に何日分もまとめて処理された場合等に喋りすぎないため）。
    crossed_index = progress["last_notified_index"]
    for i, station in enumerate(TOKAIDO_ROUTE):
        if i <= progress["last_notified_index"]:
            continue
        if station["km"] <= new_km:
            crossed_index = i

    await save_journey_progress(new_km, crossed_index)

    if crossed_index > progress["last_notified_index"]:
        return TOKAIDO_ROUTE[crossed_index]
    return None


# ═══════════════════════════════════════════════════════
# 会話中の質問応答用Tool（get_step_history と同型）
# ═══════════════════════════════════════════════════════

@tool
async def get_journey_status() -> str:
    """まがときさんから「旅どこまで進んだ？」「今どこにいる？」「東海道の旅の様子は？」のように、
    歩数で進める東海道五十三次の旅について尋ねられたときに呼ぶツールです。
    """
    progress = await get_journey_progress()
    pos = current_position(progress["total_distance_km"])
    last = pos["last_station"]
    nxt = pos["next_station"]
    pct = round(pos["progress_ratio"] * 100, 1)

    lines = [
        f"現在地：{last['name']}（京都から{progress['total_distance_km']:.1f}km、旅の進捗{pct}%）",
    ]
    if nxt:
        remaining = nxt["km"] - progress["total_distance_km"]
        lines.append(f"次の目的地：{nxt['name']}まであと{remaining:.1f}km")
    else:
        lines.append("すでに日本橋に到達しています！旅の完遂です。")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# 通過報告（main.py の health_update_endpoint から呼ばれる）
# weather_prep_job / spot_proximity_job と同じ「LLM呼び出しなしの固定文言＋TTS」
# パターン。天気は到達地の地名でTavily検索等はせず、既存のOpenWeatherMap経路を
# 使うにはlat/lngが必要なため、まずは地名込みの固定文言のみ（天気連携は次段階）。
# ═══════════════════════════════════════════════════════

async def announce_milestone(station: dict) -> None:
    if not manager.active_connections:
        return

    note = f"（{station['note']}）" if station.get("note") else ""
    message = f"歩いた分だけ旅が進んで、「{station['name']}」に到着しました！{note}"

    audio_base64 = await generate_tts(message)
    audio_mime = (
        "audio/wav"
        if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
        else "audio/mpeg"
    )

    await manager.broadcast({
        "type":           "proactive_speech",
        "reply":          message,
        "audio_data":     audio_base64,
        "audio_mime":     audio_mime,
        "spatial_effect": "cyber",
    })
    print(f"[旅] 通過を報告しました: {station['name']}")
