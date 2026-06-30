# backend/agents/router.py

import os
from typing import List, Optional, Literal

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


# ─── 構造化出力用のデータスキーマ定義（Literalで厳格化） ───
class RouterAnalysis(BaseModel):
    intent: Literal['development', 'chat', 'schedule', 'location', 'other'] = Field(
        description="ユーザーの発話意図から最も適切なものを1つ選択"
    )

    selected_agents: List[Literal['chronicle', 'keeper', 'pulse']] = Field(
        description="引き出すべき思考エージェントのリスト（複数選択可）"
    )

    reason: str = Field(
        description="そのインテントおよびエージェントを選択した論理的な理由"
    )


# 💡 思考調停LLMの初期化
# コスト削減のため2026-06-29にgpt-4oからgpt-4o-miniへ変更。
# Routerは構造化出力（Literal型でintent/selected_agentsを厳格に分類するだけ）のため、
# 自由文生成と比べて要求される精度のハードルが低く、gpt-4o-miniでも実用上十分と判断した。
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# 構造化出力を強制バインド
structured_llm = llm.with_structured_output(RouterAnalysis)


async def analyze_and_route(
    user_message: str,
    current_time_str: str,
    sector_info: str,
    image_base64: Optional[str] = None
) -> RouterAnalysis:
    """
    ユーザー発話・現在時刻・セクター・画像情報から
    どの思考エージェントを呼び出すべきか判定する高精度ルーター
    """

    system_instruction = (
        "あなたはXR随伴型AIパートナー『ルキルキ』の思考調停ルーターです。\n\n"
        "ユーザーの発話内容、現在の状況（時刻・セクター）、および提供された画像情報（存在する場合）から、"
        "ユーザーの『発話意図（intent）』を分類し、起動すべき『思考エージェント（selected_agents）』を正しく選択してください。\n\n"

        "【発話意図（intent）の分類基準】\n"
        "- 'development': Unity、Blender、FastAPI、ソースコード、バグ修正、開発環境に関する話題。\n"
        "- 'chat': 日常の雑談、挨拶、感情的な交流、特に目的のない会話。\n"
        "- 'schedule': イベント予定、カレンダー、スケジュール管理に関する話題。\n"
        "- 'location': 現在地、特定のセクター、周辺スポットや地理的な位置に関する話題。\n"
        "- 'other': 上記のどれにも分類できない特殊な要求。\n\n"

        "【思考エージェント（selected_agents）の役割】\n"
        "- 'chronicle': 外部イベント、最新ニュース、Webリサーチ、技術動向などの調査が必要な場合。\n"
        "- 'keeper': Unity/Blenderなどの3Dツール、コードのデバッグ、開発環境エラー、実装相談など技術的な知識が必要な場合。\n"
        "- 'pulse': 時間帯に合わせた気遣い、日常の雑談、GPS/位置情報への反応、ユーザーへの寄り添いが必要な場合。\n\n"

        f"【現在時刻】\n{current_time_str}\n\n"
        f"【現在セクター】\n{sector_info}\n"
    )

    content_list = [
        {
            "type": "text",
            "text": f"【ユーザー発話】\n{user_message}"
        }
    ]

    # 画像がある場合のみVision入力を適切にフォーマットして追加
    if image_base64:
        if not image_base64.startswith("data:image/"):
            image_base64 = f"data:image/jpeg;base64,{image_base64}"

        content_list.append({
            "type": "image_url",
            "image_url": {
                "url": image_base64
            }
        })

    messages = [
        SystemMessage(content=system_instruction),
        HumanMessage(content=content_list)
    ]

    try:
        response = await structured_llm.ainvoke(messages)
        return response

    except Exception as e:
        print(f"[Router Error] ルーティング解析中にエラーが発生しました: {e}")

        # エラー発生時の安全なフォールバック
        return RouterAnalysis(
            intent="chat",
            selected_agents=["pulse"],
            reason="ルーター内部障害、またはバリデーション失敗による安全なフォールバック"
        )