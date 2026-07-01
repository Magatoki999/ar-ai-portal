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
from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch
from langchain_core.tools import tool

from agents.state import RukirukiState
from agents.router import analyze_and_route

# ─── 起動時に一度だけ読み込む知識ベース（毎リクエスト読み込みを防ぐ） ───
from services.persona import load_magatoki_context as _load_knowledge
_MAGATOKI_KNOWLEDGE_CACHE: str = _load_knowledge()

# ─── LLM・ツール初期化 ───
# モデルは .env の LLM_MODEL_SMART / LLM_MODEL_FAST で一括管理する。
# 省略時のデフォルト値はここで定義しているが、.env を変更するだけで全ノードに反映される。
# LLM_MODEL_SMART : 高精度優先（Synthesizer用）。デフォルト gpt-4o
# LLM_MODEL_FAST  : コスト優先（Router/Agent/Evaluator等）。デフォルト gpt-4o-mini
_MODEL_SMART = os.getenv("LLM_MODEL_SMART", "gpt-4o")
_MODEL_FAST  = os.getenv("LLM_MODEL_FAST",  "gpt-4o-mini")

llm_synth = ChatOpenAI(
    model=_MODEL_SMART,
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
llm_fast = ChatOpenAI(
    model=_MODEL_FAST,
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
search_tool = TavilySearch(max_results=2)  # type: ignore

from services.location import locate_current_position
from services.calendar import get_my_schedule
from services.memory import get_today_ai_news
from services.books import get_book_history

# ─── クエリ精緻化プロンプト（元 main.py から移動） ───
from langchain_core.prompts import ChatPromptTemplate as _CPT
query_refine_prompt = _CPT.from_messages([
    ("system",
     "あなたはWeb検索クエリ最適化の専門家です。"
     "ユーザーの質問と現在地情報をもとに、最も関連性の高い検索結果が得られる"
     "日本語または英語の検索クエリを1つだけ出力してください。"
     "クエリ以外の説明文は一切出力しないでください。"),
    ("human",
     "現在地: 緯度{lat} 経度{lng} 周辺住所キーワード: {address}\n"
     "元のクエリ: {base_query}"),
])


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

    # services/* から解決（main.py への逆依存を完全排除）
    from services.persona  import load_rukiruki_persona, build_dynamic_constraints
    from services.location import fetch_street_address

    MAGATOKI_KNOWLEDGE = _MAGATOKI_KNOWLEDGE_CACHE
    # identity_context から呼び名を抽出（正規表現を使わずシンプルな文字列探索で実装）
    identity_ctx = state.get("identity_context", "")
    user_call = "まがとき"
    marker = "\u547c\u3073\u540d"  # "呼び名"
    sep1, sep2 = "\uff1a", ":"       # 全角コロン / 半角コロン
    for sep in (sep1, sep2):
        idx = identity_ctx.find(marker + sep)
        if idx != -1:
            rest = identity_ctx[idx + len(marker) + 1:].lstrip()
            # 先頭の括弧類を除去
            for bracket in ("\u300e", "\u300c", "\u300f", "\u300d", "\u300a", "\u300b"):
                rest = rest.lstrip(bracket)
            # 空白・改行・閉じ括弧で区切る
            end = len(rest)
            for ch in (" ", "\n", "\u300d", "\u300f", "\u300b", "\u3011"):
                pos = rest.find(ch)
                if pos != -1 and pos < end:
                    end = pos
            candidate = rest[:end].strip()
            if candidate:
                user_call = candidate
                break
    system_constraints = build_dynamic_constraints(user_call, state.get("episode_context", ""))
    main_search_tool   = search_tool
    llm_with_tools     = llm_synth.bind_tools(
        [search_tool, locate_current_position, get_my_schedule, get_today_ai_news, get_book_history]
    )

    base_persona = load_rukiruki_persona(user_call)

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
        f"{state.get('system_constraints_override') or system_constraints}"
        f"{state.get('spot_context', '')}"
        f"{state.get('calendar_context', '')}"
        f"{state.get('growth_context', '')}"
        f"{state.get('emotion_context', '')}"
        f"{state.get('episode_context', '')}"
        f"{state.get('meal_context', '')}"
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
    # 「これ」「何」などの単独の指示語・疑問詞は日常会話にも頻出し、無関係な発話でも
    # マッチして画像を過剰に解釈してしまう（机の上の物等に意図せず言及する）原因になっていた。
    # そのため「画像を見てほしい」という意図が明確なフレーズのみに絞っている。
    vision_keywords = [
        "見て", "みてください", "これ何", "これなに", "これは何",
        "何が見える", "何か見える", "何が映って", "何が写って",
        "写ってる", "写ってます", "映ってる", "映ってます",
        "視覚で", "カメラに",
    ]
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
                elif tool_call["name"] == "get_my_schedule":
                    schedule_result = await get_my_schedule.ainvoke(tool_call["args"])
                    messages.append(ToolMessage(
                        content=str(schedule_result),
                        tool_call_id=str(tool_call["id"])
                    ))
                elif tool_call["name"] == "get_today_ai_news":
                    ai_news_result = await get_today_ai_news.ainvoke(tool_call["args"])
                    messages.append(ToolMessage(
                        content=str(ai_news_result),
                        tool_call_id=str(tool_call["id"])
                    ))
                elif tool_call["name"] == "get_book_history":
                    book_history_result = await get_book_history.ainvoke(tool_call["args"])
                    messages.append(ToolMessage(
                        content=str(book_history_result),
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

        # SPOT_PROPOSALタグ抽出
        spot_proposal = ""
        spot_match = re.search(r"\|\|SPOT_PROPOSAL:(.*?)\|\|", ai_reply)
        if spot_match:
            spot_proposal = spot_match.group(1).strip()
            ai_reply = re.sub(r"\|\|SPOT_PROPOSAL:.*?\|\|", "", ai_reply).strip()

        # SHOW_IMAGEタグ抽出（記憶写真をフロントに表示）
        show_image_url = ""
        image_match = re.search(r"\|\|SHOW_IMAGE:(.*?)\|\|", ai_reply)
        if image_match:
            show_image_url = image_match.group(1).strip()
            ai_reply = re.sub(r"\|\|SHOW_IMAGE:.*?\|\|", "", ai_reply).strip()

        # ENGRAVEタグ検出（記憶を永遠に刻む）
        engrave_triggered = bool(re.search(r"\|\|ENGRAVE\|\|", ai_reply))
        ai_reply = re.sub(r"\|\|ENGRAVE\|\|", "", ai_reply).strip()

        # SEARCH_LOCATION_PHOTOタグ処理
        # evaluatorに渡す前にai_replyからは除去するが、タグ自体はai_replyの末尾に再付加して
        # main.pyのpost処理（DB検索→URLセット）に引き渡す
        loc_match = re.search(r"\|\|SEARCH_LOCATION_PHOTO:(.*?)\|\|", ai_reply)
        if loc_match:
            loc_name = loc_match.group(1).strip()
            # ai_replyからタグを除去してevaluatorの減点を防ぐ
            ai_reply = re.sub(r"\|\|SEARCH_LOCATION_PHOTO:.*?\|\|", "", ai_reply).strip()
            # show_image_urlが未取得の場合のみ、main.pyが検索できるようタグを末尾に再付加
            if not show_image_url:
                ai_reply = ai_reply + f" ||SEARCH_LOCATION_PHOTO:{loc_name}||"

        # NOTE: messages に AIMessage を追加しない。
        # add_messages リデューサーで蓄積すると次回 synthesizer 実行時に
        # state["messages"] に前回返答が混入して ai_reply が重複するため。
        # 会話履歴管理は main.py の payload.history で完結させる。
        return {
            "ai_reply": ai_reply,
            "spatial_effect": spatial_effect,
            "spot_proposal": spot_proposal,
            "engrave_triggered": engrave_triggered,
            "show_image_url": show_image_url,
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

    2026-06-26追加: 同じLLM呼び出しの中で、返答の感情（facial_emotion）も分類する。
    追加のLLM呼び出しを増やさず、品質評価と同時に判定することでコストを抑える狙い。
    facial_emotion は RukiFaceIcon（マーカーロスト中の顔アイコン）の表情切り替えに使われる。
    """
    ai_reply = state.get("ai_reply", "")
    retry_count = state.get("retry_count", 0)

    # 最大2回まで（3回目以降は必ずOKにして無限ループを防ぐ）。
    # facial_emotion はこの場合判定できないため neutral 扱いにする
    # （フロント側では aiStatus が thinking/idle のときは無視されるため実害はない）。
    if retry_count >= 2 or not ai_reply:
        return {"eval_score": 10, "facial_emotion": "neutral"}

    prompt = (
        "あなたはルキルキの返答品質評価者です。\n"
        "以下の返答について、①品質評価（0〜10点）と②セリフの意味合いから読み取れる感情、"
        "の2つをJSON形式で出力してください。\n"
        "出力は厳密にJSONのみ（説明文や前置き、Markdownのコードブロックは禁止）。\n\n"
        '{"score": 0〜10の整数, "facial_emotion": "fun/sad/worry/angry/neutralのいずれか"}\n\n'
        "①品質評価の減点基準:\n"
        "- URLが含まれている (-5点)\n"
        "- 100文字を大きく超えて長すぎる (-2点)\n"
        "- 日本語として不自然 (-3点)\n"
        "- エフェクトタグが残っている (-2点)\n\n"
        "②facial_emotionの判定基準（セリフ全体のトーンで判断。複数当てはまる場合は最も強い感情を選ぶ）:\n"
        "- fun: 楽しい・嬉しい・笑っている・はしゃいでいるトーン\n"
        "- sad: 寂しい・切ない・残念がっているトーン\n"
        "- worry: 心配・不安・気がかりなトーン\n"
        "- angry: 怒り・ムッとしている・ツッコミが強いトーン\n"
        "- neutral: 上記のどれにも明確に当てはまらない、落ち着いた・普通のトーン\n\n"
        f"返答:\n{ai_reply}\n\n"
        "JSON:"
    )
    try:
        res = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        clean = re.sub(r"```json|```", "", res.content.strip()).strip()

        try:
            data = json.loads(clean)
            score = int(data.get("score", 10))
            facial_emotion = data.get("facial_emotion", "neutral")
        except (json.JSONDecodeError, ValueError, TypeError):
            # JSON解析に失敗した場合、数字だけは抜き出せる可能性があるのでフォールバックする
            match = re.search(r"\d+", clean)
            score = int(match.group()) if match else 10
            facial_emotion = "neutral"
            print(f"[Evaluator] JSON解析失敗。scoreのみ抽出: {clean[:100]}")

        score = max(0, min(10, score))
        if facial_emotion not in ("fun", "sad", "worry", "angry", "neutral"):
            facial_emotion = "neutral"

        print(f"[Evaluator] score={score} retry={retry_count} facial_emotion={facial_emotion}")

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
        # arweave-python-client が未インストールの場合は即スキップ
        engrave_triggered = state.get("engrave_triggered", False)
        _arweave_available = bool(os.getenv("ARWEAVE_JWK"))
        try:
            import importlib.util as _ilu
            if _ilu.find_spec("arweave") is None:
                _arweave_available = False
        except Exception:
            _arweave_available = False
        if _arweave_available and (engrave_triggered or (score >= 8 and mood)):
            reason = "ENGRAVEコマンド" if engrave_triggered else f"高品質({score})×感情({mood})"
            print(f"[Arweave] 永続化条件を満たしました（理由: {reason}）")
            arweave_tx_id = await save_to_arweave(state)

        return {"eval_score": score, "arweave_tx_id": arweave_tx_id, "facial_emotion": facial_emotion}
    except Exception as e:
        print(f"[Evaluator Node Error] {e}")
        return {"eval_score": 10, "arweave_tx_id": "", "facial_emotion": "neutral"}


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
