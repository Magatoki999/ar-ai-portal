"""
services/glasses/hud_status.py

Even G2等、音声入出力を持たない「表示専用」グラス向けのHUD配信。
services/state.py の manager（/ws/avatar・AR画面向け）とは完全に別の
購読リストを持ち、そちらの動作・宛先には一切影響しない。

main.chat_endpoint 内で応答が確定するたびに broadcast_hud() を呼ぶ想定。
スマホ版・AR版どちらの会話であっても同じように配信されるため、
Even G2は「どの画面で交わした会話か」を意識せず、常に最新の
ルキルキの発言をミラー表示するだけの立ち位置になる。
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.glasses.schemas import HudStatus, ExpressionCode

router = APIRouter()


class HudConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_hud(self, facial_emotion: str, text: str):
        try:
            expression = ExpressionCode(facial_emotion)
        except ValueError:
            expression = ExpressionCode.IDLE

        payload = HudStatus(expression=expression, short_text=text[:40]).model_dump()

        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except Exception:
                pass


# main.py からもimportして使うシングルトン
glasses_hud_manager = HudConnectionManager()


@router.websocket("/ws/glasses/hud")
async def glasses_hud_endpoint(websocket: WebSocket):
    """
    Even G2等、表示専用グラス向けの購読エンドポイント。
    クライアント側からは何も送らず、受信のみを行う想定
    （接続維持のため receive_text() で待機するだけ）。
    """
    await glasses_hud_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        glasses_hud_manager.disconnect(websocket)