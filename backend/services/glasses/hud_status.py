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

from services.glasses.schemas import HudStatus, ExpressionCode, truncate_for_hud
from services import state

router = APIRouter()


class HudConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        # 直近に配信したpayloadを覚えておき、新規接続時に即座に送る
        # （broadcast_hudは会話が発生した時にしか呼ばれないため、これが無いと
        # 新しく繋いだグラスは次の会話まで歩数バッジ等が空のままになってしまう）。
        self._last_payload: dict | None = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if self._last_payload is not None:
            try:
                await websocket.send_json(self._last_payload)
            except Exception:
                pass

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_hud(self, facial_emotion: str, text: str, user_text: str = ""):
        try:
            expression = ExpressionCode(facial_emotion)
        except ValueError:
            expression = ExpressionCode.IDLE

        payload = HudStatus(
            expression=expression,
            short_text=truncate_for_hud(text),
        ).model_dump()

        # ユーザーの発話文（2026-08-14追加、HUD全面刷新の「YOU //」枠用）。
        # 長すぎるとcanvas描画側で折り返しが破綻しかねないので同じtruncateを適用。
        payload["user_text"] = truncate_for_hud(user_text) if user_text else ""

        # 歩数バッジ（2026-08-10追加）。HudStatusスキーマ自体は変更せず、
        # model_dump()の結果に後からキーを足す形にしている
        # （services/glasses/schemas.pyへの変更を避けるため）。
        # 歩数記録が無い場合は空文字にし、main.ts側で非表示にする。
        step_badge = f"{state.latest_step_count:,}歩" if state.latest_step_count is not None else ""
        if state.latest_journey_summary:
            step_badge = f"{step_badge}\n{state.latest_journey_summary}" if step_badge else state.latest_journey_summary
        payload["step_badge"] = step_badge
        payload["journey_ratio"] = state.latest_journey_ratio if state.latest_journey_ratio is not None else 0.0

        self._last_payload = payload

        dead_connections: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except Exception:
                dead_connections.append(connection)
        for connection in dead_connections:
            self.disconnect(connection)
        if dead_connections:
            print(f"[G2 HUD] dead connections cleaned: {len(dead_connections)} (active={len(self.active_connections)})")


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
    print(f"[G2 HUD] connected (active={len(glasses_hud_manager.active_connections)})")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[G2 HUD] WebSocket error: {e}")
    finally:
        glasses_hud_manager.disconnect(websocket)
        print(f"[G2 HUD] disconnected (active={len(glasses_hud_manager.active_connections)})")
