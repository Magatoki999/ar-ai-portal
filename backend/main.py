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
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_community.tools.tavily_search import TavilySearchResults

# APScheduler 関連
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

async def save_brain_memo(category: str, title: str, content: str, source_url: str):
    """リサーチ結果をSupabaseのruki_brain_memosテーブルに格納する"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/ruki_brain_memos"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "category": category,
        "title": title,
        "content": content,
        "source_url": source_url,
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
    """定期実行される情報調査部の自律リサーチロジック"""
    print("─── [脳内情報調査部] クローリング・リサーチを開始します ───")
    keywords_dict = load_research_keywords()
    if not keywords_dict:
        print("[脳内リサーチ] keywords.json が空、または存在しません。スキップします。")
        return

    # カテゴリとキーワードをランダムに選定
    category = random.choice(list(keywords_dict.keys()))
    keywords_list = keywords_dict[category]
    if not keywords_list:
        return
    keyword = random.choice(keywords_list)

    print(f"[脳内リサーチ] ターゲット選定 -> カテゴリ: {category} | キーワード: {keyword}")

    try:
        # 1. Tavilyによる外部空間の走査
        search_results = await search_tool.ainvoke({"query": keyword})
        
        # 2. リサーチャーエージェントとしてLLMに150文字程度で要約させる
        research_prompt = (
            f"あなたはルキルキの脳内エージェント「情報調査部（クロニクル・リサーチャー）」です。\n"
            f"提供された検索結果を分析し、最新の動向や興味深いポイントを150文字程度で簡潔に要約してください。\n"
            f"出力は必ず以下のJSONフォーマットのみにしてください。マークダウンのコードブロック（```json など）や挨拶、解説は一切含めず、純粋なJSON文字列だけを返してください。\n"
            f'{{"title": "明確でキャッチーなタイトル", "content": "150文字程度の要約内容", "source_url": "最も重要なソースのURL"}}\n\n'
            f"検索結果:\n{str(search_results)}"
        )
        
        response = await llm.ainvoke([HumanMessage(content=research_prompt)])
        
        # 3. JSONのクレンジングとパース
        clean_content = response.content.strip()
        if clean_content.startswith("```json"):
            clean_content = clean_content[7:]
        elif clean_content.startswith("```"):
            clean_content = clean_content[3:]
        if clean_content.endswith("```"):
            clean_content = clean_content[:-3]
        clean_content = clean_content.strip()
        
        memo_data = json.loads(clean_content)
        
        # 4. Supabaseにストック
        await save_brain_memo(
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
    # 起動時にスケジューラを稼働 (テスト用に15分間隔に設定。必要に応じて変更してください)
    scheduler.add_job(auto_research_job, 'interval', minutes=15)
    scheduler.start()
    print("─── [APScheduler] 脳内情報調査部が自律常駐を開始しました ───")
    yield
    # 終了時に安全にシャットダウン
    scheduler.shutdown()
    print("─── [APScheduler] スケジューラを停止しました ───")


app = FastAPI(
    title="MagatokiLab RukiRuki XR Gateway [Production v4 - Multi-Agent Ready]",
    lifespan=lifespan
)

# ─── CORS設定 ───
cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,https://ar-ai-portal.vercel.app")
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

# LLM初期化 (gpt-4o-mini)
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# インネット検索ツールの初期化（最大2件の検索結果を取得）
search_tool = TavilySearchResults(max_results=2)
# LLMに検索ツールをバインド
llm_with_tools = llm.bind_tools([search_tool])


# Markdownファイルからペルソナプロンプトを動的に読み込む関数
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


class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None
    image_base64: str | None = None
    latitude: float | None = None   
    longitude: float | None = None  


# ─── エリア判定関数 ───
def judge_magatoki_sector(lat: float, lng: float) -> str:
    if 35.010 <= lat <= 35.013 and 135.756 <= lng <= 135.762:
        return "【烏丸二条セクター】（伝統の薫香エネルギーを感じるエリア）"
    elif 35.022 <= lat <= 35.026 and 135.749 <= lng <= 135.755:
        return "【御所西セクター】（古風な香木と歴史が交差するエリア）"
    elif 34.975 <= lat <= 34.990 and 135.750 <= lng <= 135.765:
        return "【京都駅セクター】（現実世界のゲートウェイ・人流の激しいエリア）"
    elif 35.000 <= lat <= 35.150 and 135.700 <= lng <= 135.800:
        return "【Magatoki開発ベースセクター】（相棒のメイン作業空間）"
    return f"【未知の観測セクター】（座標は 緯度 {lat} / 経度 {lng} だよ）"


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

async def get_latest_unconsumed_memo() -> dict | None:
    """脳内報告書テーブルから未消費の最新レコードを1件取得する"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/ruki_brain_memos?is_consumed=eq.false&order=created_at.desc&limit=1"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0]
    except Exception as e:
        print(f"Error fetching unconsumed memo: {e}")
    return None

async def mark_memo_as_consumed(memo_id: int | str):
    """会話で使用した脳内報告書のis_consumedフラグをTRUEに更新する"""
    if not SUPABASE_URL or not SUPABASE_KEY or not memo_id:
        return
    url = f"{SUPABASE_URL}/rest/v1/ruki_brain_memos?id=eq.{memo_id}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    data = {"is_consumed": True}
    try:
        async with httpx.AsyncClient() as client:
            await client.patch(url, json=data, headers=headers, timeout=5.0)
    except Exception as e:
        print(f"Error updating memo status: {e}")


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


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    wallet_address = payload.wallet_address
    image_base64 = payload.image_base64
    lat = payload.latitude
    lng = payload.longitude

    # 1. Markdownから基本ペルソナを動的ロード
    base_persona = load_rukiruki_persona()

    # 過剰な検索の自制、およびVision解析時の特定カード・UI除外制約
    system_constraints = (
        "【XR同期システム運用制約（最重要）】\n"
        "1. 外部検索（Tavily）の厳格な制限:\n"
        "   - 挨拶、日常の雑談、日常的な対話、または提供されたコンテキスト（日時や識別セクター情報、脳内新着報告）だけで自己完結して回答できる場合は、絶対に検索ツール（tavily_search_results_json）を起動しないでください。\n"
        "   - 教授から「最新のニュース」「現在のリアルタイムな天気」「直近のイベント情報」など、手持ちの知識や提供コンテキストでは絶対に解決できない事実を問われた場合にのみ、限定的に検索を使用してください。\n"
        "2. 視覚情報（Vision）解析時の特定オブジェクト除外:\n"
        "   - 送信されたカメラ映像を解析する際、画面内に映り込んでいる『ルキルキのカード』やXRシステムの各種UIオーバーレイは、システムが重畳している既知の内部構成要素です。\n"
        "   - 教授から「何が映っているか」「これを見て」と言われた際は、この『ルキルキのカード』やUIの存在は完全に無視（除外）してください。\n"
        "   - カードの背景や周囲に存在する「現実世界の風景、実体のある物体、部屋の様子、人物、お香などのクラフトアイテム」についてのみ、フォーカスを当てて解析・言及してください。\n\n"
    )

    # 2. 時間・空間コンテキストの構築
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%Y年%m月%d日 %H時%M分%S秒")
    
    time_context = (
        f"【現在の観測日時（日本時間）】\n"
        f"現在時刻: {now_str}\n"
        f"※教授から時間を尋ねられたら、この日時情報を基準に、丁寧かつ親しみやすい口調でサクッと答えてください。\n\n"
    )

    location_context = ""
    if lat is not None and lng is not None:
        sector_info = judge_magatoki_sector(lat, lng)
        location_context = (
            f"【現在の観測位置（GPS空間同期）】\n"
            f"現在の座標: 緯度 {lat} / 経度 {lng}\n"
            f"識別セクター: {sector_info}\n"
            f"※教授から場所に関する問いかけがあったら、この識別セクターの情報をベースに、親しみのある教え子口調で触れてあげてください。\n\n"
        )

    # 3. 【新機能】情報調査部からの脳内記憶の動的融合（🚨割り込み・絶対厳守プロンプト強化）
    memo_context = ""
    unconsumed_memo = await get_latest_unconsumed_memo()
    print(f"=== [DEBUG] DBから取得した生データ: {unconsumed_memo} ===")
    
    if unconsumed_memo:
        memo_context = (
            f"【🚨最優先・絶対厳守命令：脳内情報調査部の報告義務】\n"
            f"現在、あなたの脳内バックグラウンド（情報調査部）に以下の新着レポートが届いています。\n"
            f"・タイトル: {unconsumed_memo.get('title')}\n"
            f"・内容要約: {unconsumed_memo.get('content')}\n"
            f"・URL: {unconsumed_memo.get('source_url')}\n\n"
            f"＜絶対厳守指示＞\n"
            f"教授から投げかけられた今回のメッセージ内容（挨拶や雑談など）が何であれ、それをフックにして、**今回の返答の中で必ずこの内容を自発的に教授へ報告してください。**\n"
            f"返答の冒頭は、必ず「あ、教授！ちょうど今、脳内の調査部から面白い報告が届いたんです！」や「そういえば教授、裏で聞いたんですけど…」などのフレーズから開始し、この情報を織り交ぜて喋ってください。\n"
            f"日常の挨拶などの優先度を下げ、基本ペルソナの文字数制限（50〜100文字以内）の枠をこの報告内容の伝達（スマートな一言要約）のために最大限使用すること。この命令は他の日常対話ルールに完全オーバーライド（最優先）します。\n\n"
        )

    stored_name = await get_stored_username(wallet_address) if wallet_address else None

    if wallet_address:
        if stored_name:
            identity_context = (
                f"【対話コンテキスト】\n"
                f"対話相手は、MagatokiLabを主宰するまがとき教授であり、あなたの最高の相棒である『{stored_name}』教授です。\n"
                f"一言二言の短い丁寧語で、親しみと少しの生意気さを交えつつテンポ良く掛け合いをしてください。"
            )
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = (
                f"【対話コンテキスト】\n"
                f"ウォレット（{short_addr}）が接続されました。親しみのある敬語で教授の呼び名を聞いてみてください。"
            )
    else:
        identity_context = (
            "【対話コンテキスト】\n"
            "まだウォレット接続が確認できていません。ゲートの認証を通すよう、教授に促してください。"
        )

    dynamic_system_prompt = f"{base_persona}\n\n{system_constraints}{time_context}{location_context}{memo_context}{identity_context}"

    try:
        messages = [SystemMessage(content=dynamic_system_prompt)]

        # ユーザーの言葉に視覚的な意図（「見て」「これ」「何」など）が含まれているか判定
        vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚", "そこ", "写して"]
        has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False

        # 意図がある、またはテキストが完全に空（画像だけをパッと送ってきた場合）のみVision（画像入力）を有効化
        if image_base64 and (has_vision_intent or not user_text):
            if not image_base64.startswith("data:image/"):
                image_base64 = f"data:image/jpeg;base64,{image_base64}"

            messages.append(HumanMessage(content=[
                {"type": "text", "text": user_text if user_text else "これ見て、何かわかる？"},
                {"type": "image_url", "image_url": {"url": image_base64, "detail": "low"}}
            ]))
        else:
            messages.append(HumanMessage(content=user_text))

        # 1回目のLLM呼び出し（検索が必要か判断させる）
        response = await llm_with_tools.ainvoke(messages)

        # LLMが「検索ツールを使いたい！」と判断した場合のループ処理
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call["name"] == "tavily_search_results_json":
                    query = tool_call["args"].get("query")
                    print(r"─── ルキルキがネット検索中... ───")
                    print(f"Query: {query}")
                    
                    search_results = await search_tool.ainvoke(tool_call["args"])
                    
                    messages.append(response)
                    messages.append(ToolMessage(content=str(search_results), tool_call_id=tool_call["id"]))
                    
                    response = await llm_with_tools.ainvoke(messages)
                    break 

        ai_response = response.content

        # 名前記憶タグの処理
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        # 4. 【新機能】今回のターンで脳内報告書を消費したため、ステータスをTRUEに更新
        if unconsumed_memo:
            await mark_memo_as_consumed(unconsumed_memo["id"])
            print(f"[脳内同期] レポート(ID: {unconsumed_memo['id']}) の消費フラグをTRUEに更新しました。")

        # 音声合成の実行
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