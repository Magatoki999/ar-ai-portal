# backend/agents/state.py
from typing import Annotated, Optional
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RukirukiState(TypedDict):
    """
    ルキルキのLangGraphステート定義。
    会話の一ターン全体を通じて受け渡されるデータ構造。
    """

    # ─── 会話メッセージ履歴 ───
    # add_messages により append-only で管理される
    messages: Annotated[list[BaseMessage], add_messages]

    # ─── ルーター出力 ───
    intent: str                        # 'development' | 'chat' | 'schedule' | 'location' | 'other'
    selected_agents: list[str]         # ['chronicle', 'keeper', 'pulse'] の組み合わせ

    # ─── 各エージェントの思考結果 ───
    chronicle_output: str              # 調査・ニュース系
    keeper_output: str                 # 技術知識系
    pulse_output: str                  # 感情・位置系

    # ─── コンテキスト群（プロンプトに差し込まれる） ───
    memo_context: str                  # agent_memosから取得したDBメモ
    episode_context: str               # エピソードメモリ
    emotion_context: str               # 感情ステート
    identity_context: str              # ウォレット・ユーザー名
    location_context: str              # GPS・セクター
    time_context: str                  # 現在時刻

    # ─── 画像・ビジョン ───
    image_base64: Optional[str]
    is_initial_greeting: bool

    # ─── 最終出力 ───
    ai_reply: str
    spatial_effect: str                # 'cyber' | 'sakura' | 'snow' | 'rain'
    active_memo_ids: list[str]

    # ─── Self-Evaluator 用 ───
    eval_score: int                    # 0〜10
    retry_count: int                   # 最大2回まで再試行

    # ─── 動的制約オーバーライド ───
    system_constraints_override: str  # ユーザー名で動的生成したconstraints

    # ─── カレンダー・成長 ───
    calendar_context: str              # 京都行事・誕生日コンテキスト
    growth_context: str                # 成長・日数コンテキスト

    # ─── メモリースポット ───
    spot_context: str                  # 近くのメモリースポット情報（プロンプト用）
    nearby_spot: Optional[dict]        # 近くのスポットのdict（なければNone）
    spot_proposal: str                 # SPOT_PROPOSALタグで抽出した場所名
    engrave_triggered: bool            # ENGRAVEタグが立ったか

    # ─── オンチェーン記憶 ───
    arweave_tx_id: str                 # 保存されたArweaveトランザクションID（保存なしは空文字）
