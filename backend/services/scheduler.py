# services/scheduler.py
# ─────────────────────────────────────────────────────────────────────────────
# APScheduler で動く定期ジョブ群。
#   - auto_research_job  : 15分ごとにキーワードを Tavily 検索して agent_memos に保存
#   - proactive_talk_job : 1分ごとに無言を検知して自発的に話しかける
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import json
import asyncio
import random
from datetime import datetime, timedelta, timezone

import httpx

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_tavily import TavilySearch

import services.state as state
from services.tts import generate_tts
from services.emotion import (
    build_emotion_context,
    get_calendar_context,
    get_growth_context,
)
from services.memory import save_agent_memo


search_tool = TavilySearch(max_results=2)


# ─── ルキルキ ペルソナ読み込み ───
def load_rukiruki_persona(user_call: str = "まがとき") -> str:
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                raw = f.read()
            return raw.replace("{USER_CALL}", f"「{user_call}」")
        except Exception:
            pass
    return (
        f"あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        f"{user_call}さんの随伴AIとして、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
    )


def _load_research_keywords() -> dict:
    keywords_path = "keywords.json"
    if os.path.exists(keywords_path):
        try:
            with open(keywords_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {keywords_path}: {e}")
    return {}


# ─── 定期リサーチジョブ ───
async def auto_research_job(llm) -> None:
    print("─── [脳内情報調査部] クローリング・リサーチを開始します ───")
    keywords_dict = _load_research_keywords()
    if not keywords_dict:
        return

    category     = random.choice(list(keywords_dict.keys()))
    keywords_list = keywords_dict[category]
    if not keywords_list:
        return
    keyword = random.choice(keywords_list)

    try:
        search_results = await search_tool.ainvoke({"query": keyword})
        research_prompt = (
            "あなたはルキルキの脳内エージェント「情報調査部（クロニクル・リサーチャー）」です。\n"
            "提供された検索結果を分析し、最新の動向や興味深いポイントを150文字程度で簡潔に要約してください。\n"
            "出力は必ず以下のJSONフォーマットのみにしてください。\n"
            '{"title": "明確でキャッチーなタイトル", "content": "150文字程度の要約内容", '
            '"source_url": "最も重要なソースのURL"}\n\n'
            f"検索結果:\n{str(search_results)}"
        )
        response    = await llm.ainvoke([HumanMessage(content=research_prompt)])
        clean       = re.sub(r"```json|```", "", response.content.strip()).strip()
        memo_data   = json.loads(clean)
        await save_agent_memo(
            agent_name="chronicle",
            category=category,
            title=memo_data.get("title", f"{keyword}に関する調査報告"),
            content=memo_data.get("content", ""),
            source_url=memo_data.get("source_url", ""),
        )
        print(f"[脳内リサーチ] 成果レポートをDBに格納しました: {memo_data.get('title')}")
    except Exception as e:
        print(f"[脳内リサーチ] リサーチプロセスでエラーが発生しました: {e}")


# ─── 自発発話ジョブ ───
_PROACTIVE_CONSTRAINTS = (
    "【ルキルキ自発システム発話制約】\n"
    "1. あなたは、今まがときさんの隣に漂っているAIパートナーとして、自発的にひとりごとや雑談を発話します。\n"
    "2. まがときさんからの質問への返答ではないため、連続質問攻めにせず、独り言・ネット情報報告・"
    "時間帯への感想・気遣い・自分の気分などを優しく呟いてください。\n"
    "3. 文字数は50〜100文字以内で短く、親しみのある丁寧語でまとめてください。URLは絶対に出力禁止です。\n"
    "4. 【重要】会話の雰囲気や時間帯、内容に合わせて、セリフの末尾に必ず空間エフェクト指示タグを "
    "『||EFFECT:エフェクト名||』 の形式で埋め込んでください。\n"
    "   - 指定可能なエフェクト名は [sakura, snow, rain, cyber] の4つのみです。\n"
    "5. まがときさんが『覚えて』と言ったとき必ず ||ENGRAVE|| タグをセリフ末尾に追加してください。\n"
    "6. まがときさんが「写真を見せて」などと言ったとき、エピソードに[image:URL]が含まれていれば "
    "||SHOW_IMAGE:URL|| をセリフ末尾に追加してください。\n\n"
)


async def proactive_talk_job(llm, magatoki_knowledge: str) -> None:
    """
    1分ごとに呼ばれる。60秒以上無言かつ AR マーカー認識中であれば自発発話を生成する。
    """
    if not state.manager.active_connections:
        return
    if not state.is_target_found:
        print("[自発発話スキップ] ターゲットロスト中")
        return

    silence = (datetime.now(timezone.utc) - state.last_user_interaction).total_seconds()
    if silence < 60:
        return

    print("─── [ルキルキ自発同期コア] まがときさんへの話し掛けを生成中... ───")

    base_persona = load_rukiruki_persona()
    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%H時%M分")

    # 未消費のエージェントメモを1件取得
    fetched_memo = None
    memo_id_to_consume = None
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if supabase_url and supabase_key:
        url_q = (
            f"{supabase_url}/rest/v1/agent_memos"
            f"?is_consumed=eq.false&order=created_at.desc&limit=1"
        )
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url_q, headers=headers, timeout=3.0)
                if res.status_code == 200 and res.json():
                    fetched_memo       = res.json()[0]
                    memo_id_to_consume = fetched_memo.get("id")
        except Exception as e:
            print(f"[自発エラー] DB取得失敗（日常雑談にフォールバックします）: {e}")

    topic_input = (
        f"【現在時刻】: {now_str}\n"
        + (
            f"【脳内の最新インプットデータ】:\n"
            f"・カテゴリ: {fetched_memo.get('category')}\n"
            f"・トピック: {fetched_memo.get('title')}\n"
            f"・内容: {fetched_memo.get('content')}\n\n"
            "指示: 上記の最新ネット情報を咀嚼し、まがときさんに「さっき脳内でこんなの見つけたよ！」"
            "という風に、何気ない会話として優しく教えてあげてください。"
            if fetched_memo
            else (
                "指示: 現在の時間帯、またはルキルキとしての気分に絡めて、まがときさんに優しく"
                "一言、何気ない日常の独り言を話しかけてください。"
            )
        )
    )

    try:
        messages = [
            SystemMessage(
                content=(
                    f"{base_persona}\n\n"
                    f"{_PROACTIVE_CONSTRAINTS}"
                    f"{build_emotion_context()}"
                    f"{get_calendar_context()}"
                    f"{get_growth_context()}"
                    f"【対話対象】: まがときさん\n\n"
                    f"【世界観】\n{magatoki_knowledge}\n\n"
                    f"【現在の状況と発話トリガー】\n{topic_input}"
                )
            )
        ]
        response    = await llm.ainvoke(messages)
        ai_reply    = response.content.strip()

        spatial_effect = "cyber"
        effect_match   = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_reply)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
        ai_reply = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_reply).strip()

        audio_base64 = await generate_tts(ai_reply)

        audio_mime = (
            "audio/wav"
            if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
            else "audio/mpeg"
        )
        await state.manager.broadcast(
            {
                "type":           "proactive_speech",
                "reply":          ai_reply,
                "audio_data":     audio_base64,
                "audio_mime":     audio_mime,
                "spatial_effect": spatial_effect,
            }
        )
        print(f"[ルキルキ自発同期成功] 発話内容: {ai_reply} [Effect: {spatial_effect}]")

        state.last_user_interaction = datetime.now(timezone.utc)

        if memo_id_to_consume and supabase_url and supabase_key:
            patch_headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{supabase_url}/rest/v1/agent_memos?id=eq.{memo_id_to_consume}",
                    json={"is_consumed": True},
                    headers=patch_headers,
                )
    except Exception as e:
        print(f"[ルキルキ自発同期エラー] {e}")


async def trigger_proactive_speech(llm, magatoki_knowledge: str) -> None:
    """フロントエンドからの無言検知リクエストに応答して自発発話を生成する。"""
    try:
        print("─── [ルキルキ自発同期コア] 1分間の無言を検知。自発発話を生成します ───")
        await proactive_talk_job(llm, magatoki_knowledge)
    except Exception as e:
        print(f"[自発発話トリガーエラー] 処理に失敗しました: {e}")
