# backend/agents/nodes.py
"""
ルキルキ LangGraph ノード定義。
各ノードは RukirukiState を受け取り、更新した差分辞書を返す。
"""

import os
import re
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

from agents.state import RukirukiState
from agents.router import analyze_and_route

# ─── LLM・ツール初期化 ───
# Synthesizerは高精度優先（gpt-4o）
llm_synth = ChatOpenAI(
    model="gpt-4o",
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
# エージェント・評価は軽量（gpt-4o-mini）
llm_fast = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
search_tool = TavilySearchResults(max_results=2)


# ─── ① Router Node ───
async def router_node(state: RukirukiState) -> dict:
    """
    ユーザー発話を分析し intent と selected_agents を確定する。
    既存の router.py をそのまま活用。
    """
    # messages の最後の HumanMessage からユーザー発話を取得
    user_text = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            if isinstance(msg.content, str):
                user_text = msg.content
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        user_text = block["text"]
                        break
            break

    time_context = state.get("time_context", "")
    location_context = state.get("location_context", "")
    image_base64 = state.get("image_base64")

    try:
        router_res = await analyze_and_route(
            user_text or "[画像送信のみ]",
            time_context,
            location_context,
            image_base64
        )
        return {
            "intent": router_res.intent,
            "selected_agents": router_res.selected_agents
        }
    except Exception as e:
        print(f"[Router Node Error] {e}")
        return {
            "intent": "chat",
            "selected_agents": ["pulse"]
        }


# ─── ② Chronicle Node（調査・ニュース） ───
async def chronicle_node(state: RukirukiState) -> dict:
    """
    DBメモおよびWebの最新情報を収集してテキストにまとめる。
    selected_agents に 'chronicle' が含まれる場合に意味を持つ。
    """
    if "chronicle" not in state.get("selected_agents", []):
        return {"chronicle_output": ""}

    memo_context = state.get("memo_context", "")
    if not memo_context:
        return {"chronicle_output": ""}

    prompt = (
        "あなたはルキルキの思考エージェント『クロニクル（Chronicle）』です。\n"
        "以下のDBメモ情報を読み込み、まがときさんへの会話に活かせる重要ポイントを\n"
        "3点以内の箇条書きで簡潔に整理してください。URLは絶対に含めないでください。\n\n"
        f"{memo_context}"
    )
    try:
        res = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        return {"chronicle_output": res.content.strip()}
    except Exception as e:
        print(f"[Chronicle Node Error] {e}")
        return {"chronicle_output": ""}


# ─── ③ Keeper Node（技術知識） ───
async def keeper_node(state: RukirukiState) -> dict:
    """
    開発・技術系の話題に対して専門知識を整理する。
    selected_agents に 'keeper' が含まれる場合に意味を持つ。
    """
    if "keeper" not in state.get("selected_agents", []):
        return {"keeper_output": ""}

    user_text = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            if isinstance(msg.content, str):
                user_text = msg.content
            break

    if not user_text:
        return {"keeper_output": ""}

    prompt = (
        "あなたはルキルキの思考エージェント『キーパー（Keeper）』です。\n"
        "Unity、Blender、FastAPI、LangChain、WebAR（MindAR/A-Frame）などの\n"
        "技術領域の専門家として、以下の発話に関連する技術的なポイントや\n"
        "注意点を100文字以内で端的にまとめてください。\n\n"
        f"発話: {user_text}"
    )
    try:
        res = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        return {"keeper_output": res.content.strip()}
    except Exception as e:
        print(f"[Keeper Node Error] {e}")
        return {"keeper_output": ""}


# ─── ④ Pulse Node（感情・位置・時間帯） ───
async def pulse_node(state: RukirukiState) -> dict:
    """
    時間帯・感情・位置情報をもとに、返答のトーンや気遣いの方針を整理する。
    常に実行される（雑談の核）。
    """
    emotion_context = state.get("emotion_context", "")
    time_context = state.get("time_context", "")
    location_context = state.get("location_context", "")

    prompt = (
        "あなたはルキルキの思考エージェント『パルス（Pulse）』です。\n"
        "以下の状況情報をもとに、今のまがときさんへの接し方・気遣いの方針を\n"
        "1〜2文で提案してください。\n\n"
        f"{emotion_context}\n{time_context}\n{location_context}"
    )
    try:
        res = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        return {"pulse_output": res.content.strip()}
    except Exception as e:
        print(f"[Pulse Node Error] {e}")
        return {"pulse_output": ""}


# ─── ⑤ 並列エージェント実行ラッパー ───
async def run_agents_parallel(state: RukirukiState) -> dict:
    """
    Chronicle / Keeper / Pulse を asyncio.gather で並列実行し、結果を統合する。
    """
    results = await asyncio.gather(
        chronicle_node(state),
        keeper_node(state),
        pulse_node(state),
        return_exceptions=True
    )
    merged = {}
    for r in results:
        if isinstance(r, dict):
            merged.update(r)
    return merged


# ─── ⑥ Synthesizer Node（返答生成） ───
async def synthesizer_node(state: RukirukiState) -> dict:
    """
    全エージェントの出力・コンテキストを統合してルキルキの返答を生成する。
    Tool Call（Tavily / geopy）もここで処理する。
    """
    from langchain_core.prompts import ChatPromptTemplate

    # main.py からインポートされる関数・変数（実行時に解決）
    from main import (
        load_rukiruki_persona, MAGATOKI_KNOWLEDGE,
        llm_with_tools, search_tool as main_search_tool,
        fetch_street_address, query_refine_prompt,
        system_constraints
    )

    base_persona = load_rukiruki_persona()

    # エージェント出力をプロンプトに統合
    agent_insights = ""
    if state.get("chronicle_output"):
        agent_insights += f"【クロニクル調査結果】\n{state['chronicle_output']}\n\n"
    if state.get("keeper_output"):
        agent_insights += f"【キーパー技術知識】\n{state['keeper_output']}\n\n"
    if state.get("pulse_output"):
        agent_insights += f"【パルス接し方方針】\n{state['pulse_output']}\n\n"

    dynamic_system_prompt = (
        f"{base_persona}\n\n"
        f"【MagatokiLab公式設定・世界観アーカイブ】\n{MAGATOKI_KNOWLEDGE}\n\n"
        f"{system_constraints}"
        f"{state.get('spot_context', '')}"
        f"{state.get('emotion_context', '')}"
        f"{state.get('episode_context', '')}"
        f"{state.get('time_context', '')}"
        f"{state.get('location_context', '')}"
        f"{agent_insights}"
        f"{state.get('memo_context', '')}"
        f"{state.get('identity_context', '')}"
    )

    messages = [SystemMessage(content=dynamic_system_prompt)]

    # 会話履歴を注入（HumanMessage / AIMessage のみ）
    is_initial = state.get("is_initial_greeting", False)
    if not is_initial:
        for msg in state["messages"][:-1]:   # 最後のユーザー発話は後で追加
            if isinstance(msg, (HumanMessage, AIMessage)):
                messages.append(msg)

    # 最後のユーザーメッセージ（Vision対応）
    last_human = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_human = msg
            break

    image_base64 = state.get("image_base64")
    vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚"]
    user_text = ""
    if last_human:
        if isinstance(last_human.content, str):
            user_text = last_human.content
        elif isinstance(last_human.content, list):
            for block in last_human.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    user_text = block["text"]
                    break

    has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False

    if image_base64 and (has_vision_intent or is_initial or not user_text):
        if not image_base64.startswith("data:image/"):
            image_base64 = f"data:image/jpeg;base64,{image_base64}"
        vision_text = user_text if user_text else "これ見て、何かわかる？"
        if not is_initial:
            vision_text += "\n\n(※システム絶対指示: 画像内のARカード等は完全無視し、現実の物体のみに言及してください。)"
        messages.append(HumanMessage(content=[
            {"type": "text", "text": vision_text},
            {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}}
        ]))
    else:
        messages.append(HumanMessage(content=user_text or ""))

    # LLM呼び出し（Tool Call対応）
    lat = state.get("_lat")
    lng = state.get("_lng")

    try:
        response = await llm_with_tools.ainvoke(messages)

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tool_call in response.tool_calls:
                if tool_call["name"] == "tavily_search_results_json":
                    base_query = tool_call["args"].get("query")
                    if lat is not None and lng is not None:
                        address_keywords = await fetch_street_address(lat, lng)
                        if not address_keywords:
                            address_keywords = "（日本の主要都市周辺）"
                        refine_chain = query_refine_prompt | llm_fast
                        refined = await refine_chain.ainvoke({
                            "lat": lat, "lng": lng,
                            "address": address_keywords,
                            "base_query": base_query
                        })
                        tool_call["args"]["query"] = refined.content.strip()
                    search_results = await main_search_tool.ainvoke(tool_call["args"])
                    messages.append(ToolMessage(
                        content=str(search_results),
                        tool_call_id=str(tool_call["id"])
                    ))
                elif tool_call["name"] == "locate_current_position":
                    t_lat = tool_call["args"].get("lat", lat)
                    t_lng = tool_call["args"].get("lng", lng)
                    address_result = await fetch_street_address(t_lat, t_lng)
                    if not address_result:
                        address_result = "空間の歪みにより住所を特定できませんでした。"
                    messages.append(ToolMessage(
                        content=str(address_result),
                        tool_call_id=str(tool_call["id"])
                    ))
            response = await llm_with_tools.ainvoke(messages)

        ai_reply = response.content

        # エフェクトタグ抽出
        spatial_effect = "cyber"
        effect_match = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_reply)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
            ai_reply = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_reply).strip()

        # SPOT_PROPOSALタグ抽出（場所提案タグ、フロントに通知）
        spot_proposal = ""
        spot_match = re.search(r"\|\|SPOT_PROPOSAL:(.*?)\|\|", ai_reply)
        if spot_match:
            spot_proposal = spot_match.group(1).strip()
            ai_reply = re.sub(r"\|\|SPOT_PROPOSAL:.*?\|\|", "", ai_reply).strip()

        # ENGRAVEタグ検出（記憶を永遠に刻む）
        engrave_triggered = bool(re.search(r"\|\|ENGRAVE\|\|", ai_reply))
        ai_reply = re.sub(r"\|\|ENGRAVE\|\|", "", ai_reply).strip()

        return {
            "ai_reply": ai_reply,
            "spatial_effect": spatial_effect,
            "spot_proposal": spot_proposal,
            "engrave_triggered": engrave_triggered,
            "messages": [AIMessage(content=ai_reply)]
        }

    except Exception as e:
        print(f"[Synthesizer Node Error] {e}")
        return {
            "ai_reply": "あ、すみません！空間ノイズで同期が一瞬ブレちゃいました。もう一回言ってください、まがときさん？",
            "spatial_effect": "cyber",
        }


# ─── Arweave 永続記憶ヘルパー ───
async def save_to_arweave(state: RukirukiState) -> str:
    """
    会話の記憶をArweaveに永続保存する。
    eval_score >= 8 かつ mood が excited / melancholy の場合のみ呼ばれる。
    成功時はトランザクションIDを返す。失敗時は空文字。
    """
    try:
        import arweave
        from arweave.arweave_lib import Wallet, Transaction

        import tempfile

        jwk_str = os.getenv("ARWEAVE_JWK")
        if not jwk_str:
            print("[Arweave] ARWEAVE_JWK が未設定のためスキップします")
            return ""

        # Wallet はファイルパスを受け取るため一時ファイルに書き出す
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            f.write(jwk_str)
            jwk_path = f.name

        wallet = Wallet(jwk_path)

        # 保存するデータ
        JST = timezone(timedelta(hours=+9))
        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

        # 最後のユーザー発話を取得
        user_text = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                user_text = msg.content if isinstance(msg.content, str) else "[画像]"
                break

        nearby_spot = state.get("nearby_spot")
        memory_data = {
            "project": "MagatokiLab / ArtAR",
            "character": "ルキルキ (Rukiruki)",
            "timestamp": now_str,
            "user_message": user_text[:100],
            "rukiruki_reply": state.get("ai_reply", "")[:200],
            "mood": state.get("emotion_context", "")[:50],
            "eval_score": state.get("eval_score", 0),
            "spatial_effect": state.get("spatial_effect", "cyber"),
            "location_name": nearby_spot["name"] if nearby_spot else "",
            "location_lat": nearby_spot["lat"] if nearby_spot else None,
            "location_lng": nearby_spot["lng"] if nearby_spot else None,
            "engrave_triggered": state.get("engrave_triggered", False),
        }

        tx = Transaction(wallet, data=json.dumps(memory_data, ensure_ascii=False))
        tx.add_tag("App-Name", "MagatokiLab-Rukiruki")
        tx.add_tag("Content-Type", "application/json")
        tx.add_tag("Project", "ArtAR")
        tx.add_tag("Timestamp", now_str)
        # 場所情報タグ（メモリースポット名があれば追加）
        nearby_spot = state.get("nearby_spot")
        if nearby_spot:
            tx.add_tag("Location-Name", nearby_spot.get("name", "unknown"))
            tx.add_tag("Location-Lat", str(nearby_spot.get("lat", "")))
            tx.add_tag("Location-Lng", str(nearby_spot.get("lng", "")))
        tx.sign()
        tx.send()

        # 一時ファイルを削除
        import os as _os
        try:
            _os.unlink(jwk_path)
        except Exception:
            pass

        print(f"[Arweave] 記憶を永続化しました: tx={tx.id}")
        return tx.id

    except ImportError:
        print("[Arweave] arweave-python-client が未インストールです")
        return ""
    except Exception as e:
        print(f"[Arweave] 保存エラー: {e}")
        return ""


# ─── ⑦ Self-Evaluator Node（品質チェック） ───
async def evaluator_node(state: RukirukiState) -> dict:
    """
    生成された返答を自己評価する。
    スコアが低ければ retry_count をインクリメントして再試行フラグを立てる。
    最大2回まで再試行。
    """
    ai_reply = state.get("ai_reply", "")
    retry_count = state.get("retry_count", 0)

    # 最大2回まで（3回目以降は必ずOKにして無限ループを防ぐ）
    if retry_count >= 2 or not ai_reply:
        return {"eval_score": 10}

    prompt = (
        "あなたはルキルキの返答品質評価者です。\n"
        "以下の返答を0〜10点で評価し、点数だけを出力してください。\n\n"
        "減点基準:\n"
        "- URLが含まれている (-5点)\n"
        "- 100文字を大きく超えて長すぎる (-2点)\n"
        "- 日本語として不自然 (-3点)\n"
        "- エフェクトタグが残っている (-2点)\n\n"
        f"返答:\n{ai_reply}\n\n"
        "点数（数字のみ）:"
    )
    try:
        res = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        score_text = res.content.strip()
        score = int(re.search(r"\d+", score_text).group())
        score = max(0, min(10, score))
        print(f"[Evaluator] score={score} retry={retry_count}")

        # ─── Arweave永続化判定 ───
        # eval_score >= 8 かつ 感情が excited / melancholy の場合のみ保存
        arweave_tx_id = ""
        mood = ""
        emotion_ctx = state.get("emotion_context", "")
        if "excited" in emotion_ctx:
            mood = "excited"
        elif "melancholy" in emotion_ctx:
            mood = "melancholy"

        # ENGRAVEタグが立っているとき、またはスコア8以上かつ感情条件を満たすとき保存
        engrave_triggered = state.get("engrave_triggered", False)
        if engrave_triggered or (score >= 8 and mood):
            reason = "ENGRAVEコマンド" if engrave_triggered else f"高品質({score})×感情({mood})"
            print(f"[Arweave] 永続化条件を満たしました（理由: {reason}）")
            arweave_tx_id = await save_to_arweave(state)

        return {"eval_score": score, "arweave_tx_id": arweave_tx_id}
    except Exception as e:
        print(f"[Evaluator Node Error] {e}")
        return {"eval_score": 10, "arweave_tx_id": ""}


# ─── ⑧ 条件分岐関数（should_retry） ───
def should_retry(state: RukirukiState) -> str:
    """
    evaluator_node の結果を受け、再試行するかどうかを判定する。
    スコア6未満かつretry_count < 2 なら 'retry'、それ以外は 'ok'。
    """
    score = state.get("eval_score", 10)
    retry_count = state.get("retry_count", 0)

    if score < 6 and retry_count < 2:
        print(f"[should_retry] 品質不足（score={score}）で再試行します（{retry_count + 1}回目）")
        return "retry"
    return "ok"


# ─── ⑨ retry_count インクリメントノード ───
def increment_retry(state: RukirukiState) -> dict:
    """再試行時に retry_count を +1 する"""
    return {"retry_count": state.get("retry_count", 0) + 1}
