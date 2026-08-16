# services/weather_radar.py
# ─────────────────────────────────────────────────────────────────────────────
# 雨雲レーダー画像の取得サービス。
# OpenWeatherMapの降水レーダータイルAPI（Map Layers）を使う。
# Even G2はapp.jsonのネットワーク許可がバックエンドURLのみに限定されているため、
# グラス側から直接OpenWeatherMapを叩くことはできない。このモジュールが
# バックエンド経由でタイル画像を取得し、main.pyのエンドポイントがそのまま
# 中継（プロキシ）する構成にする（APIキーをグラス側に露出させないためでもある）。
#
# 環境変数: OPENWEATHERMAP_API_KEY （services/emotion.py 等と共用）
# ─────────────────────────────────────────────────────────────────────────────
import math
import os

import httpx


def _lonlat_to_tile(lat: float, lng: float, zoom: int) -> tuple[int, int]:
    """緯度経度をスリッピーマップ（Webメルカトル）のタイル座標(x, y)に変換する。"""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


async def fetch_radar_tile(lat: float, lng: float, zoom: int = 8) -> bytes | None:
    """
    現在地周辺の降水レーダータイル画像（PNG）を取得する。
    zoomはデフォルト8（1タイルがおおよそ数十km四方に相当。Even G2の極小画面向けに
    広域を見せる想定で、詳細な地図というより「この辺りが降ってそう」程度の粒度）。
    取得失敗時・APIキー未設定時はNone。
    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        return None

    x, y = _lonlat_to_tile(lat, lng, zoom)
    url = f"https://tile.openweathermap.org/map/precipitation_new/{zoom}/{x}/{y}.png?appid={api_key}"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=8.0)
        if res.status_code != 200:
            print(f"[雨雲レーダー] 取得失敗: status={res.status_code}")
            return None
        return res.content
    except Exception as e:
        print(f"[雨雲レーダー] 取得エラー: {e}")
        return None
