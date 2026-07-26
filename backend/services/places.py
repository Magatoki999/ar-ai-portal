# services/places.py
# ─────────────────────────────────────────────────────────────────────────────
# 周辺スポット推薦サービス。
# Google Places API (New) の searchNearby エンドポイントを使い、現在地周辺の
# カフェ・観光地・レストラン等をルキルキが案内できるようにする。
#
# services/location.py が「今どこにいるか」（逆ジオコーディング）を担当するのに対し、
# こちらは「周辺に何があるか」（施設検索）を担当する（責務分離。location.pyには
# 手を加えていない）。
#
# 環境変数:
#   GOOGLE_PLACES_API_KEY （Google Cloud Consoleで "Places API (New)" を有効化して発行）
# ─────────────────────────────────────────────────────────────────────────────
import os
from math import radians, sin, cos, sqrt, atan2

import httpx
from langchain_core.tools import tool

_PLACES_API_ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"

# Google Places API (New) の Table A 準拠タイプ。ユーザーの発話から緩くマッピングする。
# https://developers.google.com/maps/documentation/places/web-service/place-types
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "cafe":               ["カフェ", "喫茶店", "コーヒー"],
    "restaurant":         ["レストラン", "ご飯", "ランチ", "ディナー", "食事", "グルメ"],
    "bakery":             ["パン屋", "ベーカリー"],
    "tourist_attraction": ["観光", "名所", "スポット", "見どころ"],
    "museum":             ["美術館", "博物館"],
    "park":                ["公園"],
}

# queryが空、または上記どれにも当てはまらない場合のデフォルト検索カテゴリ。
# 「この近くのおすすめカフェ、知ってる？」のようなイメージボードの想定シーンに寄せて
# カフェ・観光地を優先しつつ、幅を持たせている。
_DEFAULT_TYPES = ["cafe", "tourist_attraction", "restaurant"]

# 徒歩の目安速度（分速メートル）。distance_m から徒歩分数を概算する際に使う。
_WALK_METERS_PER_MIN = 80


def _guess_place_types(query: str) -> list[str]:
    """ユーザーの発話（自由文）から Google Places API の type を推測する。該当なければデフォルトを返す。"""
    matched = [
        place_type
        for place_type, keywords in _CATEGORY_KEYWORDS.items()
        if any(kw in query for kw in keywords)
    ]
    return matched or _DEFAULT_TYPES


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の直線距離をメートルで概算する（外部ライブラリ不要の簡易実装）。"""
    R = 6371000.0  # 地球半径（m）
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


async def search_nearby_places(
    lat: float,
    lng: float,
    query: str = "",
    radius_m: int = 1000,
    max_results: int = 3,
) -> list[dict]:
    """
    現在地周辺の施設を検索する。
    戻り値は [{"name":, "rating":, "user_rating_count":, "address":, "types":, "distance_m":}] のリスト。
    取得失敗時・APIキー未設定時は空リストを返す（呼び出し側でエラーハンドリング不要にするため。
    services/calendar.py の get_upcoming_events() と同じ設計方針）。
    """
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print("[Places] GOOGLE_PLACES_API_KEY が未設定です")
        return []

    place_types = _guess_place_types(query)

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        # FieldMaskは必須仕様。使うフィールドだけに絞ることで課金対象データを最小化する。
        "X-Goog-FieldMask": (
            "places.displayName,places.rating,places.userRatingCount,"
            "places.formattedAddress,places.location,places.types"
        ),
    }
    payload = {
        "includedTypes": place_types,
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        },
        "languageCode": "ja",
        "rankPreference": "POPULARITY",
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                _PLACES_API_ENDPOINT, json=payload, headers=headers, timeout=8.0
            )
        if res.status_code != 200:
            print(f"[Places] 検索失敗: status={res.status_code} body={res.text[:200]}")
            return []
        data = res.json()
    except Exception as e:
        print(f"[Places] 検索エラー: {e}")
        return []

    results = []
    for place in data.get("places", [])[:max_results]:
        loc = place.get("location") or {}
        place_lat, place_lng = loc.get("latitude"), loc.get("longitude")
        distance_m = (
            _haversine_m(lat, lng, place_lat, place_lng)
            if place_lat is not None and place_lng is not None
            else None
        )
        results.append({
            "name": (place.get("displayName") or {}).get("text", "（名称不明）"),
            "rating": place.get("rating"),
            "user_rating_count": place.get("userRatingCount"),
            "address": place.get("formattedAddress", ""),
            "types": place.get("types", []),
            "distance_m": round(distance_m) if distance_m is not None else None,
        })
    return results


# ═══════════════════════════════════════════════════════
# 会話中の質問応答用Tool（get_my_schedule / get_book_history と同型）
# LLMが必要と判断したときだけ呼ばれるため、雑談中に余計なAPIコストは発生しない。
# ═══════════════════════════════════════════════════════

@tool
async def find_nearby_places(lat: float, lng: float, query: str = "") -> str:
    """まがときさんから「この近くのおすすめカフェ」「周辺に観光スポットある？」「この辺でご飯食べれるとこ」のように、
    現在地周辺の施設（カフェ・レストラン・観光地など）を尋ねられたときに呼ぶツールです。

    Args:
        lat: まがときさんの現在の緯度。システムプロンプトに提示されている現在の座標値を渡してください。
        lng: 同上、現在の経度。
        query: まがときさんが求めているジャンル（例:「カフェ」「観光スポット」「ランチ」）。
               自由文でよく、特定できない場合は空のままで構いません
               （その場合はカフェ・観光地・レストランを幅広く検索します）。

    雑談や、周辺施設と無関係な話題、または場所の名前を尋ねられただけ
    （locate_current_positionが適切な場合）では呼ばないでください。
    """
    if not lat or not lng:
        return "現在地の座標が取得できていないため、周辺検索ができませんでした。"

    places = await search_nearby_places(lat=lat, lng=lng, query=query)
    if not places:
        return "この近くでは、それらしいスポットが見つかりませんでした。"

    lines = []
    for p in places:
        rating_part = f"・評価{p['rating']}" if p.get("rating") else ""
        distance_part = (
            f"・徒歩約{max(1, round(p['distance_m'] / _WALK_METERS_PER_MIN))}分"
            if p.get("distance_m") is not None
            else ""
        )
        lines.append(f"- {p['name']}{rating_part}{distance_part}")

    return "近くで見つかったスポット:\n" + "\n".join(lines)
