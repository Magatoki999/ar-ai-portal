# backend/agents/router.py

import os
from typing import List, Optional

from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


# ─── 構造化出力用のデータスキーマ定義 ───
class RouterAnalysis(BaseModel):
    intent: str = Field(
        description="ユーザーの発話意図 ('development', 'chat', 'schedule', 'location', 'other')"
    )

    selected_agents: List[str] = Field(
        description="引き出すべき思考エージェントのリスト"
    )

    reason: str = Field(
        description="そのエージェントを選択した理由"
    )


# 💡 思考調停LLM
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

structured_llm = llm.with_structured_output(RouterAnalysis)


async def analyze_and_route(
    user_message: str,
    current_time_str: str,
    sector_info: str,
    image_base64: Optional[str] = None
) -> RouterAnalysis:
    """
    ユーザー発話・現在時刻・セクター・画像情報から
    どの思考エージェントを呼び出すべきか判定する
    """

    system_instruction = (
        "あなたはXR随伴型AIパートナー『ルキルキ』の"
        "思考調停ルーターです。\n\n"

        "以下の3つのエージェントから"
        "必要なものを選択してください。\n\n"

        "【エージェント】\n"
        "- chronicle:\n"
        "  外部イベント、最新ニュース、"
        "  リサーチ系話題。\n\n"

        "- keeper:\n"
        "  Unity、Blender、FastAPI、"
        "  エラー修正、コード問題、"
        "  開発環境系。\n\n"

        "- pulse:\n"
        "  日常会話、時間帯、"
        "  GPS、気遣い。\n\n"

        f"【現在時刻】\n{current_time_str}\n\n"
        f"【現在セクター】\n{sector_info}\n"
    )

    content_list = [
        {
            "type": "text",
            "text": f"【ユーザー発話】\n{user_message}"
        }
    ]

    # 画像がある場合のみVision入力追加
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
        print(f"[Router Error] {e}")

        # フォールバック
        return RouterAnalysis(
            intent="chat",
            selected_agents=["pulse"],
            reason="ルーター障害時のフォールバック"
        )
