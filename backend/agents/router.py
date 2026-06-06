# backend/agents/router.py
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

class RouterAnalysis(BaseModel):
    intent: str = Field(description="ユーザーの発話意図 ('development', 'chat', 'schedule', 'location', 'other')")
    selected_agents: List[str] = Field(description="引き出すべき思考エージェントのリスト ('chronicle', 'keeper', 'pulse' から複数選択可)")
    reason: str = Field(description="そのエージェントを選択した、または選択しなかった理由の言語化")

llm = ChatOpenAI(
    model="gpt-4o-mini", 
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
    ユーザーの発話内容、現在の環境コンテキスト、およびアバターカメラの画像データから
    どの思考エージェントのメモリ（chronicle, keeper, pulse）を呼び出すべきかを動的に調停するマルチモーダルルーター。
    """
    
    # 【修正ポイント】ARカードに関する言及を完全に禁止・除外する文言を追加
    system_instruction = (
        "あなたはXR随伴型AIパートナー「ルキルキ」の思考調停ルーター（コンテキスト・ルーター）です。\n"
        "ユーザー（まがとき教授）のメッセージ、現在の環境情報、そして送信されたリアルタイム画像（もしあれば）から、\n"
        "裏側で稼働している3つの専門エージェントのどの情報（思考メモリ）をルキルキの人格層に引き渡すべきかをジャッジしてください。\n\n"
        "【思考エージェントの特性】\n"
        "- chronicle: 外部イベント（Vibe.KYOTO、AI DevEx等）、お香（松栄堂・山田松等）、ゲーム（MH Now、ポケGO等）の話題、または最新の外部リサーチ情報が必要な場合。\n"
        "  ⚠️絶対禁止事項: ルキルキのARカードやNFTカード、それに類する物理マーカーについての話題は完全に禁止・除外してください。\n"
        "- keeper: ローカル開発環境（Unity, Blender, MCP, Git, Markdown設定ファイル、3Dアセット、エラー解決、ソースコードの不具合）に関する話題の場合。カメラ画像にPC画面のソースコード、Unityのエラー、3Dモデルなどが映り込んでいる場合も強力に選択してください。\n"
        "- pulse: 時間帯、GPS位置セクター、体調・睡眠などの日常的な気遣いや、文脈の隠し味データ。基本的には生存確認や日常対話で常に薄く選択されます。\n\n"
        f"【現在の環境コンテキスト】\n"
        f"・観測日時: {current_time_str}\n"
        f"・識別セクター: {sector_info}\n"
    )
    
    content_list = []
    content_list.append({"type": "text", "text": f"【ユーザー発話】: {user_message}"})
    
    if image_base64:
        content_list.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_base64}"
            }
        })
        
    messages = [
        SystemMessage(content=system_instruction),
        HumanMessage(content=content_list)
    ]
    
    response = await structured_llm.ainvoke(messages)
    return response