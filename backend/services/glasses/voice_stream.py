"""
services/glasses/voice_stream.py

スマートグラス（Rokid / 将来的にMeta Ray-Ban Display等）向けの
デバイス非依存な会話WebSocketエンドポイント。

設計方針:
- 既存の main.py / agents / services には一切変更を加えない。
- 既存の /api/chat エンドポイント関数(main.chat_endpoint)をそのまま
  内部的に呼び出す「薄いラッパー」として実装する。
- ASRはデバイス側（選択肢A）で完結させ、ここではテキストのみを受け取る。
- TTSは既存の generate_tts をそのまま利用する（chat_endpoint内部で呼ばれる）。

【既知の制約・今後の検討事項】
- main.chat_endpoint は内部で services.state.manager を通じて
  /ws/avatar に接続中のブラウザ(AR)クライアントへも
  "thinking"/"talking"/"idle" ステータスをブロードキャストする。
  現状はグローバル状態を共有する単一ユーザー運用のため実害は無い想定だが、
  将来ブラウザとグラスを同時利用するケースが出てきたらセッション分離を検討する。
- 会話履歴（_session_histories）はプロセス内メモリのみで保持しており、
  Renderのスリープ復帰・デプロイ等でのプロセス再起動で消える。
  複数ユーザー対応・永続化が必要になったらSupabase等への切り出しを検討する。
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.glasses.schemas import (
    VoiceStreamTextMessage,
    VoiceStreamServerEvent,
    HudStatus,
    ExpressionCode,
    truncate_for_hud,
)

router = APIRouter()

# ─── セッションごとの簡易会話履歴（プロセス内メモリのみ・永続化なし） ───
# main.chat_endpoint の payload.history（role: "user"/"ruki"）と同じ形式で
# 直近の会話を保持し、同一セッション内での短期記憶を成立させる。
_session_histories: dict[str, list[dict]] = {}
_MAX_HISTORY_TURNS = 20  # 直近20往復まで（それ以前はepisode_contextの長期記憶に委ねる）


def _get_history(session_id: str) -> list[dict]:
    return _session_histories.get(session_id, [])


def _append_history(session_id: str, role: str, text: str) -> None:
    history = _session_histories.setdefault(session_id, [])
    history.append({"role": role, "text": text})
    if len(history) > _MAX_HISTORY_TURNS * 2:
        del history[: len(history) - _MAX_HISTORY_TURNS * 2]


def _to_expression_code(facial_emotion: str) -> ExpressionCode:
    """
    main.chat_endpoint が返す facial_emotion 文字列を ExpressionCode に変換する。
    既存の RukiFaceIcon と同じ語彙のため基本はそのまま対応。
    未知の値が来た場合は idle にフォールバックする。
    """
    try:
        return ExpressionCode(facial_emotion)
    except ValueError:
        return ExpressionCode.IDLE


async def _process_text_message(msg: VoiceStreamTextMessage) -> VoiceStreamServerEvent:
    """
    デバイス側ASRで確定したテキストを受け取り、既存の /api/chat ロジックを
    そのまま呼び出して応答(テキスト＋TTS音声＋表情)を返す。
    main.py側との循環参照を避けるため、ここで遅延importする
    （main.py側の変更は一切不要）。
    """
    from main import chat_endpoint, ChatMessage  # 遅延import

    payload = ChatMessage(
        message=msg.text,
        wallet_address=None,  # グラス用の認証方式は後続Stepで検討
        image_base64=None,    # カメラ画像は今回のスコープ外（会話特化のため）
        latitude=None,
        longitude=None,
        history=_get_history(msg.session_id),
    )

    result = await chat_endpoint(payload)

    if result.get("status") != "success":
        return VoiceStreamServerEvent(
            event_type="error",
            session_id=msg.session_id,
            error=result.get("message", "不明なエラーが発生しました"),
        )

    reply_text = result.get("reply", "")
    _append_history(msg.session_id, "user", msg.text)
    _append_history(msg.session_id, "ruki", reply_text)

    hud = HudStatus(
        expression=_to_expression_code(result.get("facial_emotion", "idle")),
        short_text=truncate_for_hud(reply_text),
    )

    return VoiceStreamServerEvent(
        event_type="tts_audio",
        session_id=msg.session_id,
        text=reply_text,
        audio_base64=result.get("audio_data"),
        hud=hud,
    )


@router.websocket("/ws/glasses/voice")
async def glasses_voice_endpoint(websocket: WebSocket):
    """
    グラス専用のWebSocketエンドポイント。既存の /ws/avatar とは完全に
    独立しており、ARマーカー同期・自発発話のロジックには影響しない。
    """
    await websocket.accept()
    print("[Glasses WS] グラスデバイスが接続しました。")
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                msg = VoiceStreamTextMessage(**raw)
            except Exception as e:
                await websocket.send_json({
                    "event_type": "error",
                    "session_id": raw.get("session_id", ""),
                    "error": f"不正なメッセージ形式です: {e}",
                })
                continue

            event = await _process_text_message(msg)
            await websocket.send_json(event.model_dump())

    except WebSocketDisconnect:
        print("[Glasses WS] グラスデバイスが切断しました。")
