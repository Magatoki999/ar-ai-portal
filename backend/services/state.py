# services/state.py
# ─────────────────────────────────────────────────────────────────────────────
# アプリ全体で共有するグローバル状態を一元管理するモジュール。
# 循環インポートを防ぐため、他の services/* からは絶対にこのモジュールを
# import するだけにし、ここから他の services/* を import しないこと。
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
from fastapi import WebSocket


# ─── WebSocket 接続マネージャー ───
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()

# ─── ARマーカー認識状態 ───
# True のときのみ自発発話を行う
is_target_found: bool = False

# ─── 感情ステートマシン ───
emotional_state: dict = {
    "mood": "calm",
    "energy": 0.8,
    "last_shift": datetime.now(timezone.utc),
    "shift_reason": "起動時の初期状態",
}

# ─── 天気キャッシュ（30分ごとに更新） ───
weather_cache: dict = {
    "description": "",
    "temp_c": None,
    "weather_id": None,
    "city": "",
    "fetched_at": None,
    # weather_prep_job（天気ベースの自発提案）が定期実行時に予報を取得する際、
    # 会話時のような lat/lng を持たないため、最後に観測できた座標をここに保持しておく。
    "lat": None,
    "lng": None,
}

# ─── 最後にユーザーと会話した時刻 ───
last_user_interaction: datetime = datetime.now(timezone.utc)

# ─── 場所登録ペンディング状態 ───
# { wallet_address: { 'waiting': True, 'lat': float, 'lng': float } }
registration_pending: dict = {}
