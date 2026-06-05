# backend/agents/router.py
import os
from typing import List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# ─── 構造化出力用のデータスキーマ定義 ───
class RouterAnalysis(BaseModel):
    intent: str = Field(description="ユーザーの発話意図 ('development', 'chat', 'schedule', 'location', 'other')")
    selected_agents: List[str] = Field(description="引き出すべき思考エージェントのリスト ('chronicle', 'keeper', 'pulse' から複数選択可)")
    reason: str = Field(description="そのエージェントを選択した、または選択しなかった理由の言語化")


# 💡 【最適化】LLMインスタンスと構造化出力のバインドを関数の外に移動
# これにより、関数呼び出しごとの再生成を防ぎ、メモリと実行速度を大幅に改善します。
llm = ChatOpenAI(
    model="gpt-4o-mini", 
    temperature=0.0,  # 思考調停はブレをなくすため、決定論的な0.0に固定
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
structured_llm = llm.with_structured_output(RouterAnalysis)


# ─── 思考調停ルーターのプロンプトテンプレート ───
router_prompt = ChatPromptTemplate.from_template(
    "あなたはXR随伴型AIパートナー「ルキルキ」の思考調停ルーター（コンテキスト・ルーター）です。\n"
    "ユーザー（まがとき教授）のメッセージと現在の環境情報から、裏側で稼働している3つの専門エージェントの"
    "どの情報（思考メモリ）をルキルキの人格層に渡すべきかをジャッジしてください。\n\n"
    "【思考エージェントの特性】\n"
    "- chronicle: 外部イベント（Vibe.KYOTO、AI DevEx等）、お香（松栄堂・山田松等）、ゲーム（MH Now、ポケGO等）の話題、または最新の外部リサーチ情報が必要な場合。\n"
    "- keeper: ローカル開発環境（Unity, Blender, MCP, Git, Markdown設定ファイル、3Dアセット、エラー解決）に関する話題の場合。\n"
    "- pulse: 時間帯、GPS位置セクター、体調・睡眠などの日常的な気遣いや、文脈の隠し味データ。基本的には生存確認や日常対話で常に薄く選択される。\n\n"
    "【現在の環境コンテキスト】\n"
    "・観測日時: {current_time_str}\n"
    "|識別セクター: {sector_info}\n\n"
    "【ユーザー発話】\n"
    "{user_message}\n"
)


def analyze_and_route(user_message: str, sector_info: str, current_time_str: str) -> RouterAnalysis:
    """
    ユーザーのメッセージと現在の環境コンテキストから、
    どのエージェントの思考メモリを優先的に引き出すべきかを調停する（同期処理）
    """
    try:
        # プロンプトへの変数埋め込みとLLMの呼び出しをチェーン化して実行
        chain = router_prompt | structured_llm
        result = chain.invoke({
            "user_message": user_message,
            "sector_info": sector_info,
            "current_time_str": current_time_str
        })
        return result
    except Exception as e:
        print(f"[Router Error] 思考調停中にエラーが発生しました: {e}")
        # 万が一エラーが発生した場合の安全なフォールバック挙動
        return RouterAnalysis(
            intent="chat",
            selected_agents=["pulse"],
            reason="ルーター内部エラーによるセーフモード起動"
        )