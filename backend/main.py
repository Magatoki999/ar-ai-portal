import os
import base64
import re
import json
import random
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# LangChain 関連
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_community.tools.tavily_search import TavilySearchResults

# APScheduler 関連
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 🧠 思考調停ルーターのインポート
from agents.router import analyze_and_route

# 環境変数の読み込み
load_dotenv()

# ─── バックグラウンドタスク（情報調査部）の設定 ───
scheduler = AsyncIOScheduler()

def load_research_keywords() -> dict:
    """ルートディレクトリのkeywords.jsonを読み込むヘルパー関数"""
    keywords_path = "keywords.json"
    if os.path.exists(keywords_path):
        try:
            with open(keywords_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {keywords_path}: {e}")
    return {}

async def save_agent_memo(agent_name: str, category: str, title: str, content: str, source_url: str):
    """リサーチ・思考結果をSupabaseの次世代ブラックボード（agent_memosテーブル）に格納する"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/agent_memos"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "agent_name": agent_name,
        "category": category,
        "title": title,
        "content": content,
        "importance": 3,
        "metadata": {"source_url": source_url},
        "is_consumed": False
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, headers=headers, timeout=5.0)
            if res.status_code not in [200, 201]:
                print(f"[脳内エラー] Supabaseへの報告書保存に失敗しました: {res.status_code} {res.text}")
    except Exception as e:
        print(f"[脳内エラー] 保存処理中に例外が発生しました: {e}")

async def auto_research_job():
    """定期実行される情報調査部（① クロニクル・リサーチャー）の自律リサーチロジック"""
    print("─── [脳内情報調査部] クローリング・リサーチを開始します ───")
    keywords_dict = load_research_keywords()
    if not keywords_dict:
        print("[脳内リサーチ] keywords.json が空、または存在しません。スキップします。")
        return

    category = random.choice(list(keywords_dict.keys()))
    keywords_list = keywords_dict[category]
    if not keywords_list:
        return
    keyword = random.choice(keywords_list)

    print(f"[脳内リサーチ] ターゲット選定 -> カテゴリ: {category} | キーワード: {keyword}")

    try:
        search_results = await search_tool.ainvoke({"query": keyword})
        
        research_prompt = (
            f"あなたはルキルキの脳内エージェント「情報調査部（クロニクル・リサーチャー）」です。\n"
            f"提供された検索結果を分析し、最新の動向や興味興味深いポイントを150文字程度で簡潔に要約してください。\n"
            f"出力は必ず以下のJSONフォーマットのみにしてください。マークダウンのコードブロック（```json など）や挨拶、解説は一切含めず、純粋なJSON文字列だけを返してください。\n"
            f'{{"title": "明確でキャッチーなタイトル", "content": "150文字程度の要約内容", "source_url": "最も重要なソースのURL"}}\n\n'
            f"検索結果:\n{str(search_results)}"
        )
        
        response = await llm.ainvoke([HumanMessage(content=research_prompt)])
        
        clean_content = response.content.strip()
        if clean_content.startswith("```json"):
            clean_content = clean_content[7:]
        elif clean_content.startswith("```"):
            clean_content = clean_content[3:]
        if clean_content.endswith("```"):
            clean_content = clean_content[:-3]
        clean_content = clean_content.strip()
        
        memo_data = json.loads(clean_content)
        
        await save_agent_memo(
            agent_name="chronicle",
            category=category,
            title=memo_data.get("title", f"{keyword}に関する調査報告"),
            content=memo_data.get("content", ""),
            source_url=memo_data.get("source_url", "")
        )
        print(f"[脳内リサーチ] 成果レポートをDBに格納しました: {memo_data.get('title')}")
        
    except Exception as e:
        print(f"[脳内リサーチ] リサーチプロセスでエラーが発生しました: {e}")


# ─── FastAPI ライフサイクル管理（lifespan） ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(auto_research_job, 'interval', minutes=15)
    scheduler.start()
    print("─── [APScheduler] 脳内情報調査部が自律常駐を開始しました ───")
    yield
    scheduler.shutdown()
    print("─── [APScheduler] スケジューラを停止しました ───")


app = FastAPI(
    title="MagatokiLab RukiRuki XR Gateway [Production v5 - Dynamic Query Anti-Bias]",
    lifespan=lifespan
)

# ─── CORS設定 ───
cors_origins_env = os.getenv("CORS_ORIGOrigins", "http://localhost:3000,https://ar-ai-portal.vercel.app")
origins = [origin.strip() for origin in cors_origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Supabase 認証情報 ───
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

# 🧠 メインLLM（会話用・人格層: 適度な創造性を持たせるため温度高め）
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# 🧼 【根本対策用：クエリデトックス専用LLM】
# 過去の会話履歴を1ミリも渡さず、完全に決定論的(temperature=0.0)に動作させて、クエリから地名ノイズだけを削ぎ落とす
cleaner_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# インターネット検索ツールの初期化
search_tool = TavilySearchResults(max_results=2)
llm_with_tools = llm.bind_tools([search_tool])


def load_rukiruki_persona() -> str:
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"Error reading {persona_path}: {e}")
    return (
        "あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        "まがとき教授の教え子として、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
    )


def load_magatoki_context() -> str:
    combined_context = ""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    context_dir = os.path.join(base_dir, "context")
    
    if not os.path.exists(context_dir):
        print(f"⚠️ Warning: context folder not found at {context_dir}")
        return combined_context

    for root, dirs, files in os.walk(context_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        combined_context += f"\n\n=== {file} 設定始まり ===\n"
                        combined_context += f.read()
                        combined_context += f"\n=== {file} 設定終わり ===\n"
                except Exception as e:
                    print(f"❌ Failed to read {file}: {e}")
                    
    return combined_context

# ⚡ サーバー起動時にキャッシュ
MAGATOKI_KNOWLEDGE = load_magatoki_context()


class HistoryItem(BaseModel):
    role: str       # "user" または "ruki"
    text: str       # 発言内容
    timestamp: str | None = None

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None
    image_base64: str | None = None
    latitude: float | None = None   
    longitude: float | None = None  
    history: list[HistoryItem] | None = None


# ─── エリア判定関数 ───
def judge_magatoki_sector(lat: float, lng: float) -> str:
    if 35.010 <= lat <= 35.013 and 135.756 <= lng <= 135.762:
        return "【烏丸二条セクター】"
    elif 35.022 <= lat <= 35.026 and 135.749 <= lng <= 135.755:
        return "【御所西セクター】"
    elif 34.975 <= lat <= 34.990 and 135.750 <= lng <= 135.765:
        return "【京都駅セクター】"
    elif 35.020 <= lat <= 35.026 and 135.750 <= lng <= 135.760:
        return "【Magatoki開発ベースセクター】"
    return f"【未知の観測セクター】"


# 🗺️ 逆ジオコーディング（リアルタイム住所推測）
async def reverse_geocode(lat: float, lng: float) -> str:
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&accept-language=ja"
    headers = {
        "User-Agent": "MagatokiLab_RukiRuki_XR_Gateway/1.0"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                address = data.get("address", {})
                
                suburb = address.get("suburb", "")          # 区（例: 中京区）
                neighbourhood = address.get("neighbourhood", "")  # 近隣（例: 烏丸御池）
                road = address.get("road", "")              # 通り名（例: 御池通）
                
                location_text = f"{suburb} {neighbourhood} {road}".strip()
                if location_text:
                    return location_text
    except Exception as e:
        print(f"⚠️ [逆ジオコーディングエラー] 地名推測に失敗しました: {e}")
    return ""


# ─── Supabase データベースヘルパー ───
async def get_stored_username(wallet_address: str) -> str | None:
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet_address:
        return None
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?wallet_address=eq.{wallet_address.lower()}&select=user_name"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get("user_name")
    except Exception as e:
        print(f"Error fetching user name: {e}")
    return None

async def save_username_to_db(wallet_address: str, name: str):
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet_address:
        return
    url = f"{SUPABASE_URL}/rest/v1/user_profiles"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    data = {"wallet_address": wallet_address.lower(), "user_name": name}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data, headers=headers, timeout=5.0)
    except Exception as e:
        print(f"Error saving user name: {e}")

async def get_active_agent_memos(selected_agents: list) -> tuple[str, list]:
    if not SUPABASE_URL or not SUPABASE_KEY or not selected_agents:
        return "", []
    
    agents_str = ",".join(selected_agents)
    url = f"{SUPABASE_URL}/rest/v1/agent_memos?agent_name=in.({agents_str})&is_consumed=eq.false&order=created_at.desc&limit=3"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    combined_memos = ""
    memo_ids = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                memos = response.json()
                for memo in memos:
                    meta = memo.get('metadata') or {}
                    url_str = meta.get('source_url', '') if isinstance(meta, dict) else memo.get('source_url', '')
                    
                    combined_memos += f"\n【裏側エージェント共有知識 ({memo.get('agent_name')})】\n"
                    combined_memos += f"・トピック: {memo.get('category')} / {memo.get('title', '')}\n"
                    combined_memos += f"・思考内容: {memo.get('content')}\n"
                    if url_str:
                        combined_memos += f"・ソースURL: {url_str}\n"
                    
                    if "id" in memo:
                        memo_ids.append(memo["id"])
    except Exception as e:
        print(f"Error fetching active agent memos: {e}")
        
    return combined_memos, memo_ids

async def mark_memos_as_consumed(memo_ids: list):
    if not SUPABASE_URL or not SUPABASE_KEY or not memo_ids:
        return
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        for memo_id in memo_ids:
            url = f"{SUPABASE_URL}/rest/v1/agent_memos?id=eq.{memo_id}"
            try:
                await client.patch(url, json={"is_consumed": True}, headers=headers, timeout=5.0)
                print(f"[脳内同期] レポート(ID: {memo_id}) の消費フラグをTRUEに更新しました。")
            except Exception as e:
                print(f"Error updating memo status for ID {memo_id}: {e}")


# ─── 音声合成ヘルパー ───
async def generate_openai_tts(text: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: return None
    url = "https://api.openai.com/v1/audio/speech"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"model": "tts-1", "input": text, "voice": "nova"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"OpenAI TTS error: {e}")
    return None

async def generate_elevenlabs_voice(text: str) -> str | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id: return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": api_key}
    data = {"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"ElevenLabs error: {e}")
    return None


@app.get("/")
def read_root():
    return {"status": "healthy", "message": "RukiRuki Multi-Agent Dynamic Gateway Online"}


@app.post("/api/test/research")
async def trigger_research_manually():
    try:
        await auto_research_job()
        return {"status": "success", "message": "Manually triggered chronic researcher job successfully."}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    wallet_address = payload.wallet_address
    image_base64 = payload.image_base64
    lat = payload.latitude
    lng = payload.longitude

    print(f"🚨 [受信生データチェック] latitude: {lat} | longitude: {lng}")

    base_persona = load_rukiruki_persona()

    system_constraints = (
        "【XR同期システム運用制約（最重要）】\n"
        "1. 外部検索（Tavily）の厳格な制限:\n"
        "   - 挨拶、日常の雑談、日常的な対話、または提供されたコンテキストだけで自己完結して回答できる場合は、絶対に検索ツールを起動しないでください。\n"
        "   - 教授から最新情報や近くのおすすめ店など、手持ちの知識では絶対に解決できない事実を問われた場合にのみ、限定的に検索を使用してください。\n"
        "2. 視覚情報（Vision）解析時の特定オブジェクト除外:\n"
        "   - 画面内に映り込んでいる『ルキルキのカード』やXRシステムの各種UIオーバーレイは完全に無視（除外）してください。\n"
        "   - カードの背景や周囲に存在する「現実世界の風景や物体」についてのみフォーカスして解析してください。\n\n"
    )

    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%Y年%m月%d日 %H時%M分%S秒")
    
    time_context = (
        f"【現在の観測日時（日本時間）】\n"
        f"現在時刻: {now_str}\n\n"
    )

    sector_info = "【セクター未特定】"
    location_context = ""
    if lat is not None and lng is not None:
        sector_info = judge_magatoki_sector(lat, lng)
        location_context = (
            f"【現在の観測位置（GPS空間同期）】\n"
            f"現在の座標: 緯度 {lat} / 経度 {lng}\n"
            f"識別セクター: {sector_info}\n\n"
        )

    print(f"─── [Router] 思考調停を開始します ───")
    router_res = analyze_and_route(user_text if user_text else "[画像送信のみ]", sector_info, now_str)

    memo_context = ""
    active_memo_ids = []
    if router_res.selected_agents:
        fetched_memos, active_memo_ids = await get_active_agent_memos(router_res.selected_agents)
        if fetched_memos:
            memo_context = (
                f"【🧠 バックグラウンド思考層からのリアルタイム共有知識】\n"
                f"{fetched_memos}\n"
            )

    stored_name = await get_stored_username(wallet_address) if wallet_address else None

    if wallet_address:
        if stored_name:
            identity_context = (
                f"【対話コンテキスト】\n"
                f"対話相手は、まがとき教授であり、あなたの最高の相棒である『{stored_name}』教授です。"
            )
        else:
            identity_context = (
                f"【対話コンテキスト】\n"
                f"ウォレットが接続されました。親しみのある敬語で教授の呼び名を聞いてみてください。"
            )
    else:
        identity_context = "【対話コンテキスト】ウォレット接続を教授に促してください。"

    world_context = (
        f"【MagatokiLab公式設定・世界観アーカイブ】\n"
        f"{MAGATOKI_KNOWLEDGE}\n\n"
    )

    dynamic_system_prompt = f"{base_persona}\n\n{world_context}{system_constraints}{time_context}{location_context}{memo_context}{identity_context}"

    try:
        messages = [SystemMessage(content=dynamic_system_prompt)]

        if payload.history:
            for item in payload.history:
                if item.role == "user":
                    messages.append(HumanMessage(content=item.text))
                elif item.role == "ruki":
                    messages.append(AIMessage(content=item.text))

        vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚"]
        has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False

        if image_base64 and (has_vision_intent or not user_text):
            if not image_base64.startswith("data:image/"):
                image_base64 = f"data:image/jpeg;base64,{image_base64}"
            messages.append(HumanMessage(content=[
                {"type": "text", "text": user_text if user_text else "これ見て、何かわかる？"},
                {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}}
            ]))
        else:
            messages.append(HumanMessage(content=user_text if user_text else ""))

        if memo_context:
            response = await llm.ainvoke(messages)
        else:
            response = await llm_with_tools.ainvoke(messages)
        
        # 検索ツールの実行ループ
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "tavily_search_results_json":
                    raw_query = tool_call["args"].get("query")
                    
                    # 💡 【根本解決：検索ツール起動時のガードレール】
                    if lat is not None and lng is not None:
                        clean_sector = sector_info.replace("【", "").replace("】", "").split("セクター")[0]
                        
                        # 🔥 過去の会話履歴を1ミリも知らない完全独立したLLMにクエリを通し、
                        # 履歴から漏れ出した古い地名や修飾語（福知山、宮津、周辺など）を徹底デトックスする
                        clean_prompt = (
                            "あなたは検索クエリの最適化エンジンです。\n"
                            "入力された検索クエリから、あらゆる地名、駅名、市区町村名、エリア名、都道府県名、および「周辺」「近くの」といった位置に関する修飾語を完全に削除してください。\n"
                            "ユーザーが純粋に検索しようとしている『対象物・目的（例: カフェ、居酒屋、最新ニュース、お香の専門店など）』のキーワードだけを抽出してください。\n"
                            "余計な解説や挨拶、マークダウンの装飾(```json等)は一切含めず、クレンジング後の純粋なキーワードのみを出力してください。\n\n"
                            f"入力クエリ: {raw_query}"
                        )
                        
                        try:
                            clean_res = await cleaner_llm.ainvoke([HumanMessage(content=clean_prompt)])
                            base_query = clean_res.content.strip().replace("`", "").replace('"', '')
                        except Exception as e:
                            print(f"⚠️ [クエリクレンジングエラー] {e}")
                            base_query = raw_query.replace("京都市", "").strip()

                        # 最新のリアルタイム地名・住所推測（逆ジオコーディング）を取得
                        estimated_location = await reverse_geocode(lat, lng)
                        
                        if "未知の観測" in clean_sector or "Magatoki開発ベース" in clean_sector:
                            if estimated_location:
                                # 完全に現在のクリーンな住所テキストのみでクエリを上書き再構築！
                                query = f"京都市 {estimated_location} {base_query} 徒歩圏内 おすすめ"
                                print(f"✨ [空間同期] 過去の地名バイアスを完全クリア。【{estimated_location}】でクエリを再構成しました。")
                            else:
                                query = f"京都市 {lat}, {lng} 周辺 {base_query} 徒歩圏内"
                                print("⚠️ [空間同期] 地名推測が空だったため生座標を採用します。")
                        else:
                            # 登録セクター内の場合は、定義済みの美しい実在地名でガチガチに固定
                            query = f"京都市 {clean_sector} {base_query} 徒歩圏内 おすすめ"
                            print(f"✨ [空間同期] 既知セクター: 【{clean_sector}】のキーワードを適応しました。")
                        
                        tool_call["args"]["query"] = query  # クエリの書き換え上書き
                    else:
                        print("⚠️ [空間同期スキップ] 位置情報が None のためガードレールをスルー。")

                    print(r"─── ルキルキがネット検索中... ───")
                    print(f"Query: {query}")
                    
                    search_results = await search_tool.ainvoke(tool_call["args"])
                    
                    messages.append(response)
                    messages.append(ToolMessage(content=str(search_results), tool_call_id=tool_call["id"]))
                    
                    response = await llm_with_tools.ainvoke(messages)
                    break

        ai_response = response.content

        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        if active_memo_ids:
            await mark_memos_as_consumed(active_memo_ids)

        provider = os.getenv("TTS_PROVIDER", "openai").lower()
        audio_base64 = None
        if provider == "elevenlabs":
            audio_base64 = await generate_elevenlabs_voice(ai_response)
            if not audio_base64:
                audio_base64 = await generate_openai_tts(ai_response)
        else:
            audio_base64 = await generate_openai_tts(ai_response)

    except Exception as e:
        print(f"LLM/Vision/Search Error: {e}")
        ai_response = "あ、すみません！空間ノイズで同期が一瞬ブレちゃいました。もう一回言ってください、教授？"
        audio_base64 = None

    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "status": "success"
    }