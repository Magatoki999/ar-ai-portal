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
#     started_at date,
#     updated_at timestamptz not null default now()
#   );
#   insert into journey_progress (id, total_distance_km, last_notified_index)
#   values (1, 0, 0) on conflict (id) do nothing;
#   -- 既存テーブルに後から追加する場合は以下だけでよい:
#   -- alter table journey_progress add column started_at date;
#
#   create table journey_haiku_log (
#     id bigint generated always as identity primary key,
#     station_name text not null,
#     km numeric,
#     weather text,
#     haiku text not null,
#     created_at timestamptz not null default now()
#   );
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
    {"name": "三条大橋",   "km": 0.0,   "note": "旅の出発点、京都", "city": "Kyoto"},
    {"name": "大津宿",     "km": 4.0,   "note": "東海道最後（京都から見れば最初）の宿場", "city": "Otsu"},
    {"name": "草津宿",     "km": 16.0,  "note": "中山道との分岐点として栄えた宿場町", "city": "Kusatsu"},
    {"name": "石部宿",     "km": 28.0,  "note": "", "city": "Konan"},
    {"name": "水口宿",     "km": 39.0,  "note": "", "city": "Koka"},
    {"name": "土山宿",     "km": 49.0,  "note": "", "city": "Koka"},
    {"name": "坂下宿",     "km": 58.0,  "note": "鈴鹿峠の手前", "city": "Kameyama"},
    {"name": "関宿",       "km": 64.0,  "note": "今も宿場町の町並みが残る", "city": "Kameyama"},
    {"name": "亀山宿",     "km": 70.0,  "note": "", "city": "Kameyama"},
    {"name": "庄野宿",     "km": 75.0,  "note": "", "city": "Suzuka"},
    {"name": "石薬師宿",   "km": 79.0,  "note": "", "city": "Suzuka"},
    {"name": "四日市宿",   "km": 87.0,  "note": "", "city": "Yokkaichi"},
    {"name": "桑名宿",     "km": 100.0, "note": "七里の渡しで宮宿と結ばれていた港町", "city": "Kuwana"},
    {"name": "宮宿",       "km": 124.5, "note": "東海道随一の宿場町、熱田", "city": "Nagoya"},
    {"name": "鳴海宿",     "km": 132.0, "note": "", "city": "Nagoya"},
    {"name": "池鯉鮒宿",   "km": 143.0, "note": "現在の知立", "city": "Chiryu"},
    {"name": "岡崎宿",     "km": 155.0, "note": "", "city": "Okazaki"},
    {"name": "藤川宿",     "km": 162.0, "note": "", "city": "Okazaki"},
    {"name": "赤坂宿",     "km": 169.0, "note": "", "city": "Toyokawa"},
    {"name": "御油宿",     "km": 170.7, "note": "赤坂宿との間はわずか1.7km、東海道最短区間", "city": "Toyokawa"},
    {"name": "吉田宿",     "km": 178.0, "note": "現在の豊橋", "city": "Toyohashi"},
    {"name": "二川宿",     "km": 186.0, "note": "", "city": "Toyohashi"},
    {"name": "白須賀宿",   "km": 195.0, "note": "潮見坂からの絶景で知られる", "city": "Kosai"},
    {"name": "新居宿",     "km": 201.0, "note": "今切の渡し", "city": "Kosai"},
    {"name": "舞坂宿",     "km": 206.0, "note": "", "city": "Hamamatsu"},
    {"name": "浜松宿",     "km": 216.0, "note": "", "city": "Hamamatsu"},
    {"name": "見付宿",     "km": 248.0, "note": "現在の磐田", "city": "Iwata"},
    {"name": "袋井宿",     "km": 257.0, "note": "", "city": "Fukuroi"},
    {"name": "掛川宿",     "km": 266.0, "note": "", "city": "Kakegawa"},
    {"name": "日坂宿",     "km": 272.0, "note": "小夜の中山峠の手前", "city": "Kakegawa"},
    {"name": "金谷宿",     "km": 281.0, "note": "", "city": "Shimada"},
    {"name": "島田宿",     "km": 286.0, "note": "大井川の渡し", "city": "Shimada"},
    {"name": "藤枝宿",     "km": 295.0, "note": "", "city": "Fujieda"},
    {"name": "岡部宿",     "km": 302.0, "note": "", "city": "Fujieda"},
    {"name": "丸子宿",     "km": 310.0, "note": "とろろ汁で有名", "city": "Shizuoka"},
    {"name": "府中宿",     "km": 316.0, "note": "現在の静岡", "city": "Shizuoka"},
    {"name": "江尻宿",     "km": 327.0, "note": "現在の清水", "city": "Shizuoka"},
    {"name": "興津宿",     "km": 333.0, "note": "", "city": "Shizuoka"},
    {"name": "由比宿",     "km": 339.0, "note": "薩埵峠の絶景", "city": "Shizuoka"},
    {"name": "蒲原宿",     "km": 344.0, "note": "", "city": "Shizuoka"},
    {"name": "吉原宿",     "km": 352.0, "note": "", "city": "Fuji"},
    {"name": "原宿",       "km": 361.0, "note": "富士山の眺めで知られる", "city": "Numazu"},
    {"name": "沼津宿",     "km": 368.0, "note": "", "city": "Numazu"},
    {"name": "三島宿",     "km": 379.0, "note": "箱根越えの手前", "city": "Mishima"},
    {"name": "箱根宿",     "km": 392.0, "note": "東海道最大の難所、箱根八里", "city": "Hakone"},
    {"name": "小田原宿",   "km": 406.0, "note": "", "city": "Odawara"},
    {"name": "大磯宿",     "km": 417.0, "note": "", "city": "Oiso"},
    {"name": "平塚宿",     "km": 423.0, "note": "", "city": "Hiratsuka"},
    {"name": "藤沢宿",     "km": 430.0, "note": "", "city": "Fujisawa"},
    {"name": "戸塚宿",     "km": 437.0, "note": "", "city": "Yokohama"},
    {"name": "保土ヶ谷宿", "km": 445.0, "note": "", "city": "Yokohama"},
    {"name": "神奈川宿",   "km": 452.0, "note": "", "city": "Yokohama"},
    {"name": "川崎宿",     "km": 461.0, "note": "", "city": "Kawasaki"},
    {"name": "品川宿",     "km": 471.0, "note": "", "city": "Tokyo"},
    {"name": "日本橋",     "km": 495.0, "note": "東海道の終点、旅の目的地", "city": "Tokyo"},
]


def steps_to_km(steps: int, stride_m: float = DEFAULT_STRIDE_M) -> float:
    return round(steps * stride_m / 1000, 3)


async def get_weather_at_city(city: str) -> str | None:
    """
    到達地点（現代の都市名）の現在の天気を取得し、短い一言に整形して返す。
    weather_advisor.pyの予報APIとは別に、OpenWeatherMapの現在天気APIを使う
    （「今から傘が要るか」ではなく「今そこはどんな天気か」を知りたいため）。
    取得失敗時はNone（呼び出し側で天気なしのメッセージにフォールバックする）。
    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        return None

    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city},JP&appid={api_key}&units=metric&lang=ja"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=8.0)
        if res.status_code != 200:
            return None
        data = res.json()
        weather_list = data.get("weather") or []
        if not weather_list:
            return None
        description = weather_list[0].get("description", "")
        temp = data.get("main", {}).get("temp")
        if temp is not None:
            return f"{description}・気温{round(temp)}度"
        return description
    except Exception as e:
        print(f"[旅] 天気取得エラー: {e}")
        return None


_HAIKU_PROMPT = (
    "あなたはXR観測ナビゲーター「ルキルキ」です。東海道を歩いて旅する企画の一環で、"
    "たった今「{station}」に到着しました。この土地・季節・天気を詠み込んだ、"
    "松尾芭蕉を意識した五七五の俳句を1句だけ作ってください。\n"
    "【この土地の情報】{note}\n"
    "【現在の天気】{weather}\n"
    "【出力ルール】俳句のみを出力してください。前置き・説明・記号は一切不要です。"
)


async def generate_haiku(station: dict, weather: str | None) -> str | None:
    """到達地・季節・天気を詠み込んだ俳句を1句生成する。失敗時はNone。"""
    try:
        from services.resilient_llm import build_fast_llm
        llm = build_fast_llm(temperature=0.9, name="Journey-Haiku")
        prompt = _HAIKU_PROMPT.format(
            station=station["name"],
            note=station.get("note") or "（特記事項なし）",
            weather=weather or "（不明）",
        )
        response = await llm.ainvoke(prompt)
        haiku = response.content.strip()
        return haiku if haiku else None
    except Exception as e:
        print(f"[旅] 俳句生成エラー: {e}")
        return None


async def get_journey_progress() -> dict:
    """{'total_distance_km': float, 'last_notified_index': int, 'started_at': str|None} を返す。取得失敗時はゼロ初期値。"""
    url, key = _sb()
    empty = {"total_distance_km": 0.0, "last_notified_index": 0, "started_at": None}
    if not url or not key:
        return empty

    endpoint = f"{url}/rest/v1/journey_progress?id=eq.1&select=total_distance_km,last_notified_index,started_at"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            return empty
        rows = res.json()
        if not rows:
            return empty
        return {
            "total_distance_km": float(rows[0].get("total_distance_km", 0.0)),
            "last_notified_index": int(rows[0].get("last_notified_index", 0)),
            "started_at": rows[0].get("started_at"),
        }
    except Exception as e:
        print(f"[旅] 進捗取得エラー: {e}")
        return empty


async def save_journey_progress(total_distance_km: float, last_notified_index: int, started_at: str | None = None) -> bool:
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
    if started_at:
        payload["started_at"] = started_at
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


async def reset_journey() -> bool:
    """
    旅の進捗・俳句ログを完全にリセットし、三条大橋(0km)からやり直せる状態にする。
    save_journey_progress()（upsert、started_atは値がある時しか送らない）とは別に、
    started_atを明示的にnullへ戻す必要があるため、直接PATCHする専用関数にしている。
    """
    url, key = _sb()
    if not url or not key:
        return False

    ok = True
    headers = {**_sb_headers(), "Content-Type": "application/json"}

    # journey_progress を初期値に戻す（started_atも明示的にnullへ）
    try:
        async with httpx.AsyncClient() as client:
            res = await client.patch(
                f"{url}/rest/v1/journey_progress?id=eq.1",
                json={
                    "total_distance_km": 0,
                    "last_notified_index": 0,
                    "started_at": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                headers=headers,
                timeout=5.0,
            )
        if res.status_code not in (200, 204):
            print(f"[旅] リセット(進捗)失敗: status={res.status_code} body={res.text[:200]}")
            ok = False
    except Exception as e:
        print(f"[旅] リセット(進捗)エラー: {e}")
        ok = False

    # journey_haiku_log を全件削除
    try:
        async with httpx.AsyncClient() as client:
            res = await client.delete(
                f"{url}/rest/v1/journey_haiku_log?id=gte.0",
                headers=headers,
                timeout=5.0,
            )
        if res.status_code not in (200, 204):
            print(f"[旅] リセット(俳句ログ)失敗: status={res.status_code} body={res.text[:200]}")
            ok = False
    except Exception as e:
        print(f"[旅] リセット(俳句ログ)エラー: {e}")
        ok = False

    return ok


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


async def apply_realtime_steps(delta_steps: int, stride_m: float = DEFAULT_STRIDE_M) -> dict | None:
    """
    M5Atomなどのリアルタイム歩数源から届いた「増分歩数」だけを旅へ反映する。
    既存の apply_daily_steps() と同じ進捗・宿場判定・G2キャッシュ更新を再利用する。
    delta_steps <= 0 は無視する。
    """
    if delta_steps <= 0:
        return None
    return await apply_daily_steps(delta_steps, stride_m)


async def apply_daily_steps(steps: int, stride_m: float = DEFAULT_STRIDE_M) -> dict | None:
    """
    その日の歩数を旅の進捗に反映する。services/health.py の save_daily_steps 成功時に
    呼ばれる想定。新しく宿場を1つ以上通過していれば、その通過情報（一番新しいもの）を
    返す。通過が無ければNoneを返す。
    """
    from services import state as _state

    progress = await get_journey_progress()
    old_km = progress["total_distance_km"]
    new_km = old_km + steps_to_km(steps, stride_m)

    # 初回呼び出し時（started_atが未設定）は今日を旅の開始日として記録する
    started_at = progress["started_at"]
    if not started_at:
        started_at = datetime.now(timezone.utc).date().isoformat()

    # 新しく通過した地点（index）を探す。複数まとめて通過した場合は最新のものだけ通知する
    # （一気に何日分もまとめて処理された場合等に喋りすぎないため）。
    crossed_index = progress["last_notified_index"]
    for i, station in enumerate(TOKAIDO_ROUTE):
        if i <= progress["last_notified_index"]:
            continue
        if station["km"] <= new_km:
            crossed_index = i

    saved = await save_journey_progress(new_km, crossed_index, started_at)
    if not saved:
        # DB保存に失敗した場合、メモリ上のキャッシュ（Even G2表示用）だけを
        # 進めてしまうとWebページ側（DBを直接読む）とズレてしまうため、
        # ここで処理を打ち切る。次回のWebhookで再度加算が試みられる。
        print("[旅] DB保存に失敗したため、今回の進捗反映を見送りました")
        return None

    # Even G2の歩数バッジに合体表示するため、通過の有無に関わらず毎回位置情報を更新する
    pos = current_position(new_km)
    day_count = _day_count_since(started_at)
    _state.latest_journey_summary = f"{pos['last_station']['name']} {new_km:.1f}km（第{day_count}日目）"
    _state.latest_journey_ratio = pos["progress_ratio"]

    if crossed_index > progress["last_notified_index"]:
        return TOKAIDO_ROUTE[crossed_index]
    return None


def _day_count_since(started_at: str) -> int:
    """started_at（"YYYY-MM-DD"）から数えて今日が何日目かを返す（開始日を1日目とする）。"""
    try:
        start_date = datetime.fromisoformat(started_at).date()
        today = datetime.now(timezone.utc).date()
        return max(1, (today - start_date).days + 1)
    except (ValueError, TypeError):
        return 1


# main.py起動時の初期化でも使うための公開エイリアス
journey_day_count = _day_count_since


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
    day_count = _day_count_since(progress["started_at"]) if progress["started_at"] else 1

    lines = [
        f"現在地：{last['name']}（京都から{progress['total_distance_km']:.1f}km、旅の進捗{pct}%、第{day_count}日目）",
    ]
    if nxt:
        remaining = nxt["km"] - progress["total_distance_km"]
        lines.append(f"次の目的地：{nxt['name']}まであと{remaining:.1f}km")
    else:
        lines.append("すでに日本橋に到達しています！旅の完遂です。")
    return "\n".join(lines)


@tool
async def get_haiku_history(limit: int = 5) -> str:
    """まがときさんから「これまで詠んだ俳句見せて」「道中の俳句一覧」「今までの句をまとめて」のように、
    東海道の旅で道中詠んだ俳句について尋ねられたときに呼ぶツールです。

    Args:
        limit: 何句分表示するか。特に指定が無ければ5句程度でよい。
    """
    logs = await get_haiku_log(limit=limit)
    if not logs:
        return "まだ道中で詠んだ俳句はありません。"

    lines = []
    for log in logs:
        station = log.get("station_name", "")
        haiku = log.get("haiku", "")
        lines.append(f"【{station}にて】\n{haiku}")
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════
# 通過報告（main.py の health_update_endpoint から呼ばれる）
# weather_prep_job / spot_proximity_job と同じ「LLM呼び出しなしの固定文言＋TTS」
# パターン。天気は到達地の地名でTavily検索等はせず、既存のOpenWeatherMap経路を
# 使うにはlat/lngが必要なため、まずは地名込みの固定文言のみ（天気連携は次段階）。
# ═══════════════════════════════════════════════════════

async def save_haiku(station_name: str, km: float, weather: str | None, haiku: str) -> bool:
    """道中で詠んだ俳句を1件保存する。"""
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/journey_haiku_log"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    payload  = {
        "station_name": station_name,
        "km": km,
        "weather": weather,
        "haiku": haiku,
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=payload, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[旅] 俳句保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[旅] 俳句を保存しました: {station_name}")
        return True
    except Exception as e:
        print(f"[旅] 俳句保存エラー: {e}")
        return False


async def get_haiku_log(limit: int = 10) -> list[dict]:
    """道中で詠んだ俳句を、新しい順に取得する。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/journey_haiku_log"
        f"?order=created_at.desc&limit={limit}"
        f"&select=station_name,km,weather,haiku,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            return []
        return res.json()
    except Exception as e:
        print(f"[旅] 俳句取得エラー: {e}")
        return []


async def get_journey_full_status() -> dict:
    """
    journey_map_ui.html向けの集約データ。進捗・現在地・日数・全ルート・俳句ログを
    まとめて1回のAPIコールで返す（画面側の実装をシンプルにするため）。
    """
    progress = await get_journey_progress()
    pos = current_position(progress["total_distance_km"])
    day_count = _day_count_since(progress["started_at"]) if progress["started_at"] else 1
    haiku_log = await get_haiku_log(limit=20)

    return {
        "total_distance_km": progress["total_distance_km"],
        "progress_ratio": pos["progress_ratio"],
        "day_count": day_count,
        "current_station": pos["last_station"],
        "next_station": pos["next_station"],
        "route": TOKAIDO_ROUTE,
        "haiku_log": haiku_log,
    }


async def compose_haiku_for_current_station() -> dict | None:
    """
    journey_map_ui.htmlの「クリックしてその場で詠ませる」用。現在地の宿場について
    その場で俳句を生成し、announce_milestoneと同じ要領で保存する
    （通過通知の音声・broadcastは行わない、UIからの明示リクエストのため）。
    戻り値: {"station_name":, "weather":, "haiku":} または生成失敗時None。
    """
    progress = await get_journey_progress()
    pos = current_position(progress["total_distance_km"])
    station = pos["last_station"]

    weather = await get_weather_at_city(station.get("city", ""))
    haiku = await generate_haiku(station, weather)
    if not haiku:
        return None

    await save_haiku(station["name"], station["km"], weather, haiku)
    return {"station_name": station["name"], "weather": weather, "haiku": haiku}


async def announce_milestone(station: dict) -> None:
    # 注意：以前はここで「誰も接続していなければ何もしない」というガードで
    # 早期returnしていたが、それだと俳句の生成・保存まで一緒にスキップされて
    # しまっていた（歩数Webhookは深夜0時台に実行され、その時間にスマホアプリを
    # 開いている人はまず居ないため）。天気取得・俳句生成・保存は接続の有無に
    # 関わらず必ず行う。broadcast()自体は接続が無ければ何もしないので、
    # 生放送の通知だけが自然にスキップされる形になり、これで正しい。
    weather = await get_weather_at_city(station.get("city", ""))
    haiku = await generate_haiku(station, weather)
    if haiku:
        await save_haiku(station["name"], station["km"], weather, haiku)

    note = f"（{station['note']}）" if station.get("note") else ""
    weather_line = f"\nそちらは今、{weather}のようです。" if weather else ""
    haiku_line = f"\n\n{haiku}" if haiku else ""
    message = f"歩いた分だけ旅が進んで、「{station['name']}」に到着しました！{note}{weather_line}{haiku_line}"

    # TTS生成だけは、聞く人がいない深夜に無駄な音声合成APIコストを払わないよう
    # 接続がある時だけ行う（俳句の生成・保存は上ですでに完了している）。
    if not manager.active_connections:
        print(f"[旅] 通過（接続なし、俳句のみ保存）: {station['name']}")
        return

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
