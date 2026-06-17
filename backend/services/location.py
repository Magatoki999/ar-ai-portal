# services/location.py
# ─────────────────────────────────────────────────────────────────────────────
# 位置情報・逆ジオコーディング・セクター判定を担当するサービスモジュール。
# geopy による同期処理は executor でラップして非同期化する。
# ─────────────────────────────────────────────────────────────────────────────
import asyncio

from geopy.geocoders import Nominatim
from langchain_core.tools import tool


# ─── 逆ジオコーディング ───
def _sync_reverse_geocode(lat: float, lng: float) -> str:
    """geopy（同期）で緯度経度を日本語住所に変換する。"""
    try:
        geolocator = Nominatim(user_agent="magatokilab_rukiruki_gateway")
        location = geolocator.reverse((lat, lng), timeout=4, language="ja")
        if location and "address" in location.raw:
            addr = location.raw["address"]
            city = addr.get(
                "city",
                addr.get("town", addr.get("village", addr.get("province", ""))),
            )
            suburb       = addr.get("suburb", "")
            neighbourhood = addr.get("neighbourhood", "")
            attraction   = addr.get(
                "attraction", addr.get("historic", addr.get("tourism", ""))
            )
            return f"{city} {suburb} {neighbourhood} {attraction}".strip()
    except Exception as e:
        print(f"[GPS逆変換エラー] 住所の動的変換に失敗しました: {e}")
    return ""


async def fetch_street_address(lat: float, lng: float) -> str:
    """非同期ラッパー：executor でブロッキング処理を実行する。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_reverse_geocode, lat, lng)


@tool
async def locate_current_position(lat: float, lng: float) -> str:
    """まがときさんの現在の緯度・経度（lat, lng）から、実際の物理住所や周辺の有名なスポット・施設名を逆ジオコーディングで特定して返すツールです。
    まがときさんから『今どこにいる？』『現在地を教えて』『場所を特定して』など、直接場所の特定を求められた場合に、
    システムプロンプトに提示されている現在の座標値（緯度・経度）を引数に渡して呼び出してください。"""
    return await fetch_street_address(lat, lng)


# ─── セクター判定（京都エリア） ───
def judge_magatoki_sector(lat: float, lng: float) -> str:
    """GPS座標からまがとき専用セクター名を返す。"""
    if 35.010 <= lat <= 35.013 and 135.756 <= lng <= 135.762:
        return "【烏丸二条セクター】"
    if 35.022 <= lat <= 35.026 and 135.749 <= lng <= 135.755:
        return "【御所西セクター】"
    if 34.975 <= lat <= 34.990 and 135.750 <= lng <= 135.765:
        return "【京都駅セクター】"
    if 35.020 <= lat <= 35.026 and 135.750 <= lng <= 135.760:
        return "【Magatoki開発ベースセクター】"
    return "【未知の観測セクター】"
