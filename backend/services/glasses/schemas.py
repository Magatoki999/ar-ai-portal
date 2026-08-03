"""
グラス連携用の型定義（Pydanticモデル）

デバイス非依存の音声ストリーミングAPI（voice_stream.py）と、
グランサブルUI用ステータスAPI（hud_status.py）で共有する型を定義する。

設計方針:
- 既存の LangGraph / ResilientLLM / TTS ロジックには一切手を加えず、
  この層はあくまで「デバイスとの通信フォーマット」だけを定義する。
- HUD表示は情報量を極限まで絞る（表情コード＋短文1行のみ）。
  Rokidのモノクロ Micro LED、Meta Ray-Ban Display の20度視野角、
  どちらでも破綻しない最小構成として設計している。
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ExpressionCode(str, Enum):
    """
    既存の顔アイコン(public/images/)と対応する表情コード。
    グラス側には画像そのものではなく、このコード＋短文だけを送る。
    """
    IDLE = "idle"
    TALKING = "talking"
    THINKING = "thinking"
    FUN = "fun"
    SAD = "sad"
    WORRY = "worry"
    ANGRY = "angry"


class DeviceType(str, Enum):
    """接続元デバイスの識別子"""
    ROKID = "rokid"
    META_DISPLAY = "meta_display"
    WEB = "web"  # 既存のブラウザ経由での動作確認用


class HudStatus(BaseModel):
    """グラスのHUDに表示する最小限の情報"""
    expression: ExpressionCode = ExpressionCode.IDLE
    short_text: str = Field(
        default="",
        max_length=40,
        description="グランス用の短い一言。長文は送らない想定。",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class VoiceStreamStartMessage(BaseModel):
    """クライアント→サーバー: ストリーミング開始時に送るメタ情報"""
    session_id: str
    device: DeviceType
    sample_rate: int = 16000
    encoding: str = "pcm_s16le"


class VoiceStreamAudioChunk(BaseModel):
    """クライアント→サーバー: 音声チャンク(base64エンコード済みPCM)"""
    session_id: str
    audio_base64: str
    is_final: bool = False

class VoiceStreamTextMessage(BaseModel):
    """クライアント→サーバー: デバイス側ASRで確定したテキスト（Web Speech API方式を踏襲）"""
    session_id: str
    text: str
    device: DeviceType


class VoiceStreamServerEvent(BaseModel):
    """サーバー→クライアント: 応答イベント(音声・テキスト・表情の複合)"""
    event_type: str  # "transcript" | "tts_audio" | "hud_update" | "error"
    session_id: str
    text: Optional[str] = None
    audio_base64: Optional[str] = None
    hud: Optional[HudStatus] = None
    error: Optional[str] = None