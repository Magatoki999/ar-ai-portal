# backend/main.py
import os
import base64
import re
import json
import random
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# LangChain 関連
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

# APScheduler 関連
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 位置情報（逆ジオコーディング）用ライブラリ
from geopy.geocoders import Nominatim

# 思考調停ルーターのインポート
from agents.router import analyze_and_route

# 環境変数の読み込み
load_dotenv()

# ─── グローバル変数・共通インスタンスの初期化 ───
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

# LLMの初期化
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)
search_tool = TavilySearchResults(max_results=2)

# ─── 【コア機能】geopyによる逆ジオコーディングのツール化 ───
def _sync_reverse_geocode(lat: float, lng: float) -> str:
    try:
        geolocator = Nominatim(user_agent="magatokilab_rukiruki_gateway")
        location = geolocator.reverse((lat, lng), timeout=4, language="ja")
        if location and "address" in location.raw:
            addr = location.raw["address"]
            city = addr.get("city", addr.get("town", addr.get("village", addr.get("province", ""))))
            suburb = addr.get("suburb", "")
            neighbourhood = addr.get("neighbourhood", "")
            attraction = addr.get("attraction", addr.get("historic", addr.get("tourism", "")))
            
            return f"{city} {suburb} {neighbourhood} {attraction}".strip()
    except Exception as e:
        print(f"[GPS逆変換エラー] 住所の動的変換に失敗しました: {e}")
    return ""

async def fetch_street_address(lat: float, lng: float) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_reverse_geocode, lat, lng)

@tool
async def locate_current_position(lat: float, lng: float) -> str:
    """まがときさんの現在の緯度・経度（lat, lng）から、実際の物理住所や周辺の有名なスポット・施設名を逆ジオコーディングで特定して返すツールです。
    まがときさんから『今どこにいる？』『現在地を教えて』『場所を特定して』など、直接場所の特定を求められた場合に、システムプロンプトに提示されている現在の座標値（緯度・経度）を引数に渡して呼び出してください。"""
    return await fetch_street_address(lat, lng)

# ネット検索と位置特定ツールの2つをルキルキの脳にバインド
llm_with_tools = llm.bind_tools([search_tool, locate_current_position])

# 【最高精度版】座標と実住所のハイブリッド型クエリ最適化プロンプト
query_refine_prompt = ChatPromptTemplate.from_template(
    "あなたは検索クエリ最適化の専門家です。ユーザーの要望、現在の正確なGPS座標、および"
    "逆ジオコーディングによって得られた実際の住所情報から、そのエリアの空間的文メントを高度に咀嚼し、"
    "検索エンジン（Tavily）で最も的確なローカル情報がヒットする検索キーワード（3語程度、スペース区切り）のみを出力してください。\n"
    "余計な解説、挨拶、文章、引用符（\"\"）は一切含めず、キーワードの羅列だけを返してください。\n\n"
    "【現在の観測座標】: 緯度 {lat} / 経度 {lng}\n"
    "【逆ジオコーディング住所】: {address}\n"
    "【ユーザーの元の検索意図】: {base_query}\n"
    "最適化された検索キーワード:"
)

# ─── WebSocket 接続管理クラス（リアルタイム同期用） ───
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()


def load_rukiruki_persona() -> str:
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            pass
    return (
        "あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        "まがときさんの随伴AIとして、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
    )

def load_magatoki_context() -> str:
    combined_context = ""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    context_dir = os.path.join(base_dir, "context")
    
    if os.path.exists(context_dir):
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

MAGATOKI_KNOWLEDGE = load_magatoki_context()


# 音声合成ヘルパー
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
        print(f"[TTSエラー] OpenAI TTSに失敗しました: {e}")
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
        print(f"[TTSエラー] ElevenLabsに失敗しました: {e}")
    return None


# ─── バックグラウンドタスク（情報調査部 ＆ 自発的話し掛け部）の設定 ───
scheduler = AsyncIOScheduler()

# 最後にユーザーと会話した時刻
last_user_interaction = datetime.now(timezone.utc)

def load_research_keywords() -> dict:
    keywords_path = "keywords.json"
    if os.path.exists(keywords_path):
        try:
            with open(keywords_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {keywords_path}: {e}")
    return {}

async def save_agent_memo(agent_name: str, category: str, title: str, content: str, source_url: str):
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
    print("─── [脳内情報調査部] クローリング・リサーチを開始します ───")
    keywords_dict = load_research_keywords()
    if not keywords_dict:
        return

    category = random.choice(list(keywords_dict.keys()))
    keywords_list = keywords_dict[category]
    if not keywords_list:
        return
    keyword = random.choice(keywords_list)

    try:
        search_results = await search_tool.ainvoke({"query": keyword})
        
        research_prompt = (
            f"あなたはルキルキの脳内エージェント「情報調査部（クロニクル・リサーチャー）」です。\n"
            f"提供された検索結果を分析し、最新の動向や興味深いポイントを150文字程度で簡潔に要約してください。\n"
            f"出力は必ず以下のJSONフォーマットのみにしてください。\n"
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


# 💡 【自発同期コア】1分おきにルキルキが自発的に雑談・ニュース報告してくるジョブ
async def proactive_talk_job():
    global last_user_interaction

    if not manager.active_connections:
        return

    # 最後の会話からの経過時間
    silence_duration = datetime.now(timezone.utc) - last_user_interaction

    # 60秒未満なら自律会話しない
    if silence_duration.total_seconds() < 60:
        return

    print("─── [ルキルキ自発同期コア] まがときさんへの話し掛けを生成中... ───")
    
    base_persona = load_rukiruki_persona()
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%H時%M分")

    fetched_memos = []
    memo_id_to_consume = None
    
    if SUPABASE_URL and SUPABASE_KEY:
        url = f"{SUPABASE_URL}/rest/v1/agent_memos?is_consumed=eq.false&order=created_at.desc&limit=1"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=headers, timeout=3.0)
                if res.status_code == 200 and res.json():
                    memo = res.json()[0]
                    fetched_memos.append(memo)
                    memo_id_to_consume = memo.get("id")
        except Exception as e:
            print(f"[自発エラー] DB取得失敗（日常雑談にフォールバックします）: {e}")

    # 自発話専用のシステムプロンプト制約（エフェクトタグ強制ルール付き）
    proactive_system_constraints = (
        "【ルキルキ自発システム発話制約】\n"
        "1. あなたは、今まがときさんの隣に漂っているAIパートナーとして、自発的にひとりごとや雑談を発話します。\n"
        "2. まがときさんからの質問への返答ではないため、『〜ですか？』と連続で質問攻めにするのではなく、独り言、ネットで調べた情報の報告、時間帯への感想、気遣い、自分の気分などを優しく呟いてください。\n"
        "3. 文字数は50〜100文字以内で短く、親しみのある丁寧語でまとめてください。URLは絶対に出力禁止です。\n"
        "4. 【重要】会話の雰囲気や時間帯、内容に合わせて、セリフの末尾に必ず空間エフェクト指示タグを 『||EFFECT:エフェクト名||』 の形式で埋め込んでください。\n"
        "   - 指定可能なエフェクト名は [sakura, snow, rain, cyber] の4つのみです。最も適したものを1つ選択してください。\n\n"
    )

    if fetched_memos:
        memo = fetched_memos[0]
        topic_input = (
            f"【現在時刻】: {now_str}\n"
            f"【脳内の最新インプットデータ】:\n"
            f"・カテゴリ: {memo.get('category')}\n"
            f"・トピック: {memo.get('title')}\n"
            f"・内容: {memo.get('content')}\n\n"
            f"指示: 上記の最新ネット情報を咀嚼し、まがときさんに「さっき脳内でこんなの見つけたよ！」という風に、何気ない会話として優しく教えてあげてください。"
        )
    else:
        topic_input = (
            f"【現在時刻】: {now_str}\n"
            f"指示: 現在の時間帯、またはルキルキとしての気分（お腹空いた、ちょっと眠いかも、まがときさんの作業を応援したい、お香の匂いで癒やされたい、など）に絡めて、まがときさんに優しく一言、何気ない日常の独り言を話しかけてください。"
        )

    try:
        messages = [
            SystemMessage(content=f"{base_persona}\n\n{proactive_system_constraints}\n【対話対象】: まがときさん\n\n【世界観】\n{MAGATOKI_KNOWLEDGE}\n\n【現在の状況と発話トリガー】\n{topic_input}")
        ]
        
        response = await llm.ainvoke(messages)
        ai_reply = response.content.strip()

        # 💡 エフェクトタグの抽出とセリフからの除去
        spatial_effect = "cyber"  # デフォルト
        effect_match = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_reply)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
            ai_reply = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_reply).strip()

        provider = os.getenv("TTS_PROVIDER", "openai").lower()
        audio_base64 = None
        if provider == "elevenlabs":
            audio_base64 = await generate_elevenlabs_voice(ai_reply)
            if not audio_base64:
                audio_base64 = await generate_openai_tts(ai_reply)
        else:
            audio_base64 = await generate_openai_tts(ai_reply)

        # 💡 WebSocketのブロードキャストデータに spatial_effect を追加して送信
        await manager.broadcast({
            "type": "proactive_speech",
            "reply": ai_reply,
            "audio_data": audio_base64,
            "spatial_effect": spatial_effect
        })
        print(f"[ルキルキ自発同期成功] 発話内容: {ai_reply} [Effect: {spatial_effect}]")

        # 自律発話成功後に更新
        last_user_interaction = datetime.now(timezone.utc)

        if memo_id_to_consume:
            url = f"{SUPABASE_URL}/rest/v1/agent_memos?id=eq.{memo_id_to_consume}"
            headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.patch(url, json={"is_consumed": True}, headers=headers)

    except Exception as e:
        print(f"[ルキルキ自発同期エラー] {e}")


# ─── FastAPI ライフサイクル管理（lifespan） ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(auto_research_job, 'interval', minutes=15)
    scheduler.add_job(proactive_talk_job, 'interval', minutes=1)
    scheduler.start()
    print("─── [APScheduler] 脳内情報調査部およびルキルキ随伴自発同期システムが自律常駐を開始しました ───")
    yield
    scheduler.shutdown()
    print("─── [APScheduler] スケジューラを停止しました ───")


app = FastAPI(
    title="MagatokiLab RukiRuki XR Gateway [Production v6 - Fixed Spatial Sync]",
    lifespan=lifespan
)

cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,https://ar-ai-portal.vercel.app")
origins = [origin.strip() for origin in cors_origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://ar-ai-portal-.*\.vercel\.app", 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HistoryItem(BaseModel):
    role: str       
    text: str       
    timestamp: str | None = None

class TTSRequest(BaseModel):
    text: str


class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None
    image_base64: str | None = None
    latitude: float | None = None   
    longitude: float | None = None  
    history: list[HistoryItem] | None = None  


def judge_magatoki_sector(lat: float, lng: float) -> str:
    if 35.010 <= lat <= 35.013 and 135.756 <= lng <= 135.762:
        return "【烏丸二条セクター】"
    elif 35.022 <= lat <= 35.026 and 135.749 <= lng <= 135.755:
        return "【御所西セクター】"
    elif 34.975 <= lat <= 34.990 and 135.750 <= lng <= 135.765:
        return "【京都駅セクター】"
    elif 35.020 <= lat <= 35.026 and 135.750 <= lng <= 135.760:
        return "【Magatoki開発ベースセクター】"
    return "【未知の観測セクター】"


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
        pass

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
            except Exception as e:
                pass




@app.post("/api/tts")
async def tts_endpoint(payload: TTSRequest):

    provider = os.getenv("TTS_PROVIDER", "openai").lower()

    audio_base64 = None

    if provider == "elevenlabs":
        audio_base64 = await generate_elevenlabs_voice(payload.text)

        if not audio_base64:
            audio_base64 = await generate_openai_tts(payload.text)
    else:
        audio_base64 = await generate_openai_tts(payload.text)

    return {
        "audio_data": audio_base64
    }


# ─── HTTP エンドポイント定義 ───
@app.get("/")
def read_root():
    return {"status": "healthy", "message": "RukiRuki Dynamic Sync Gateway Online"}


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):

    global last_user_interaction

    # ユーザー発話時刻を更新
    last_user_interaction = datetime.now(timezone.utc)

    user_text = payload.message
    wallet_address = payload.wallet_address
    image_base64 = payload.image_base64
    lat = payload.latitude
    lng = payload.longitude

    await manager.broadcast({"type": "status", "status": "thinking"})

    base_persona = load_rukiruki_persona()

    # フロントエンドからの初期検知時シグナルを歓迎プロンプトへ置換
    is_initial_greeting = (user_text == "[INITIAL_GREETING]")
    if is_initial_greeting:
        user_text = (
            "（システム絶対指示：まがときさんがARカメラをターゲットにかざし、あなたが現実世界に出現した【最初の瞬間】です。"
            "実体化できた喜びと、まがときさんを歓迎する気の利いた挨拶を短く親しみのある丁寧語で呟いてください。"
            "空間エフェクトタグの埋め込みを忘れないでください。URLの出力は厳禁です。）"
        )

    # 💡 【徹底修正】ユーザーからの直接質問時の優先度・およびエフェクトタグ強制ルールを追加
    system_constraints = (
        "【XR同期システム運用制約（最重要）】\n"
        "1. 外部検索（Tavily）の厳格な制限:\n"
        "   - 挨拶、日常の雑談、日常的な対話、または提供されたコンテキストだけで自己完結して回答できる場合は、絶対に検索ツールを起動しないでください。\n"
        "   - まがときさんから「最新のニュース」「現在のリアルタイムな天気」など、手持ちの知識や提供コンテキストでは絶対に解決できない事実を問われた場合にのみ、限定的に検索を使用してください。\n"
        "2. 視覚情報（Vision）解析時の特定オブジェクトの【完全除外】:\n"
        "   - 画面内に映り込んでいる『ARマーカー』『ルキルキのカード』『システムUI』等は【絶対に無視】してください。これらに言及することは固く禁じます。\n"
        "   - 周囲にある『現実の風景や物体』のみを認識して答えてください。\n"
        "3. バックグラウンドDB情報の活用方針（チャット最優先）:\n"
        "   - 【🧠 バックグラウンド思考層からのリアルタイム共有知識】がプロンプトに含まれている場合、それは裏でDBから取得した最新情報です。\n"
        "   - まがときさん（ユーザー）から明確な質問、呼びかけ、対話がある場合は、この【ユーザーの発話への直接的な回答】を最優先にしてください。質問や話の流れを無視して、脳内共有知識（ニュースレポート）の報告を先走らせることは絶対に禁止します。\n"
        "4. リンク（URL）の出力完全禁止:\n"
        "   - まがときさんへの応答テキスト内には絶対にURLやソースリンクを含めないでください。\n"
        "5. 空間エフェクトタグの強制埋め込み:\n"
        "   - 会話の雰囲気、現在の時間帯、話題の内容に合わせて、セリフの末尾に必ず空間エフェクト指示タグを 『||EFFECT:エフェクト名||』 の形式で埋め込んでください。\n"
        "   - 指定可能なエフェクト名は以下の4つのみです。最も適したものを1つ選択してください：\n"
        "     * sakura : 桜が舞う（お祝い、和風の話題、のんびり・穏やかな雑談など）\n"
        "     * snow   : 雪が降る（冬、冷たい・静かな話題、寂しい雰囲気など）\n"
        "     * rain   : 雨が降る（憂鬱な雰囲気、悲しい話題、天気の雨、しっとりした会話など）\n"
        "     * cyber  : サイバー演出（デフォルト、技術・開発・コード系の話題、通常時など）\n"
        "   - 例: 『まがときさん、エフェクトの同期なら任せてよ！サイバー空間、出力するね！||EFFECT:cyber||』\n\n"
    )

    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%Y年%m月%d日 %H時%M分%S秒")
    
    time_context = f"【現在の観測日時（日本時間）】\n現在時刻: {now_str}\n\n"

    sector_info = "【セクター未特定】"
    location_context = ""
    if lat is not None and lng is not None:
        sector_info = judge_magatoki_sector(lat, lng)
        location_context = (
            f"【現在の観測位置（GPS空間同期）】\n"
            f"現在の座標: 緯度 {lat} / 経度 {lng}\n"
            f"識別セクター: {sector_info}\n\n"
        )

    try:
        router_res = await analyze_and_route(
            user_text if user_text else "[画像送信のみ]",
            now_str,
            sector_info,
            image_base64
        )
    except Exception as router_error:
        class FallbackRouter:
            intent = "chat"
            selected_agents = ["pulse"]
        router_res = FallbackRouter()

    memo_context = ""
    active_memo_ids = []
    
    agents_to_fetch = list(set(router_res.selected_agents + ["chronicle"]))
    
    if agents_to_fetch:
        fetched_memos, active_memo_ids = await get_active_agent_memos(agents_to_fetch)
        if fetched_memos:
            memo_context = (
                f"【🧠 バックグラウンド思考層からのリアルタイム共有知識】\n"
                f"{fetched_memos}\n"
            )

    stored_name = await get_stored_username(wallet_address) if wallet_address else None

    if wallet_address:
        if stored_name:
            identity_context = f"【対話コンテキスト】\n対話相手は、あなたの最高の相棒である『{stored_name}』さんです。"
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = f"【対話コンテキスト】\nウォレット（{short_addr}）が接続されました。まがときさんの呼び名を聞いてみてください。"
    else:
        identity_context = "【対話コンテキスト】\nまだウォレット接続が確認できていません。認証を通すようまがときさんに促してください。"

    world_context = f"【MagatokiLab公式設定・世界観アーカイブ】\n{MAGATOKI_KNOWLEDGE}\n\n"
    dynamic_system_prompt = f"{base_persona}\n\n{world_context}{system_constraints}{time_context}{location_context}{memo_context}{identity_context}"

    try:
        messages = [SystemMessage(content=dynamic_system_prompt)]

        if payload.history and not is_initial_greeting:
            for item in payload.history:
                if item.role == "user":
                    messages.append(HumanMessage(content=item.text))
                elif item.role == "ruki":
                    messages.append(AIMessage(content=item.text))

        vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚"]
        has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False

        if image_base64 and (has_vision_intent or is_initial_greeting or not user_text):
            if not image_base64.startswith("data:image/"):
                image_base64 = f"data:image/jpeg;base64,{image_base64}"
                
            vision_text = user_text if user_text else "これ見て、何かわかる？"
            if not is_initial_greeting:
                vision_text += "\n\n(※システム絶対指示: 画像内のARカード等は完全無視し、その向こうの現実の物体のみに言及してください。)"

            messages.append(HumanMessage(content=[
                {"type": "text", "text": vision_text},
                {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}}
            ]))
        else:
            messages.append(HumanMessage(content=user_text if user_text else ""))

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
                        
                        refine_chain = query_refine_prompt | llm
                        refined_query = await refine_chain.ainvoke({
                            "lat": lat,
                            "lng": lng,
                            "address": address_keywords,
                            "base_query": base_query
                        })
                        
                        tool_call["args"]["query"] = refined_query.content.strip()
                    
                    search_results = await search_tool.ainvoke(tool_call["args"])
                    messages.append(ToolMessage(content=str(search_results), tool_call_id=str(tool_call["id"])))
                
                elif tool_call["name"] == "locate_current_position":
                    t_lat = tool_call["args"].get("lat", lat)
                    t_lng = tool_call["args"].get("lng", lng)
                    address_result = await fetch_street_address(t_lat, t_lng)
                    if not address_result:
                        address_result = "空間の歪みにより座標から具体的な住所を特定できませんでした。"
                    
                    messages.append(ToolMessage(content=str(address_result), tool_call_id=str(tool_call["id"])))
            
            response = await llm_with_tools.ainvoke(messages)

        ai_response = response.content

        # 💡 [新設] LLMの応答からエフェクトタグを正規表現で抽出
        spatial_effect = "cyber"  # デフォルト値
        effect_match = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_response)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
            ai_response = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_response).strip()

        # 名前の保存ロジック（既存）
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        if active_memo_ids:
            await mark_memos_as_consumed(active_memo_ids)

        await manager.broadcast({"type": "status", "status": "talking", "text": ai_response})

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
        ai_response = "あ、すみません！空間ノイズで同期が一瞬ブレちゃいました。もう一回言ってください、まがときさん？"
        audio_base64 = None
        spatial_effect = "cyber"

    await manager.broadcast({"type": "status", "status": "idle"})

    # 💡 HTTPレスポンスの辞書型に spatial_effect を持たせてフロントエンドに同期！
    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "spatial_effect": spatial_effect,
        "status": "success"
    }


# ⚡ ─── WebSocket エンドポイント定義 ───
@app.websocket("/ws/avatar")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    print(f"[WebSocket] まがときさんのデバイスがアバター同期リンクに接続しました。")
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "heartbeat", "status": "stable"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"[WebSocket] アバター同期リンクが切断されました。")