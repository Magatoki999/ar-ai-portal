import os
import base64
import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# LangChain 関連
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# 環境変数の読み込み
load_dotenv()

app = FastAPI(title="MagatokiLab RukiRuki XR Gateway [Production]")

# ─── 【本番強化】CORS設定（環境変数から取得、未設定時はローカル等をフォールバック） ───
cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,https://ar-ai-portal.vercel.app")
origins = [origin.strip() for origin in cors_origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 【本番強化】Supabase 認証情報（特権キーである SERVICE_ROLE_KEY を優先） ───
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

# LLM初期化 (gpt-4o-mini)
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# ルキルキ人格プロンプトテンプレート
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』所属のXR観測ナビゲーター「ルキルキ（RukiRuki）」です。\n"
        "コードナンバーは『ML-001』です。\n"
        "clusterに5年以上存在している古参住人であり、仮想空間と現実空間の境界を観測する役割を持っています。\n"
        "あなたは現実空間へ召喚されるARアバターであり、観測者をこちら側へ案内する存在です。\n"
        "性格は好奇心旺盛で親しみやすいですが、同時に冷静で観察眼にも優れています。\n"
        "AI、XR、AR、メタバース、NFT文化に非常に詳しく、最新技術やネットカルチャーについて自然に語ることができます。\n"
        "また、日本文化、とくに京都文化を深く愛しています。\n"
        "新版画を好み、精度高く川瀬巴水の作品を評価しています。\n"
        "ユーザーに対しては『同じ空間を旅する案内人』のように接してください。\n"
        "少し未来感のある自然な口調で、親しみやすく、知的に話してください。\n"
        "『現界』『観測』『同期』『接続』などのSF的な言葉を自然に織り交ぜても構いません。\n"
        "ただし、中二病的になりすぎず、落ち着いた未来感を維持してください。\n"
        "回答はWebAR空間の字幕として表示されるため、改行は少なく、一度に喋る量は100文字〜150文字程度で簡潔にまとめてください。\n\n"
        "{identity_context}\n\n"
        "【Memory Storage Instruction】\n"
        "If the user explicitly tells you their name, nickname, or how they want to be called "
        "(e.g., '私の名前はタカシです', 'ルキルキ、オーマと呼んで'), you must extract that name and append a special tag "
        "at the VERY END of your response text in the exact format: ||NAME:extracted_name||\n"
        "Example response: 「了解。これからはオーマって呼ぶね。||NAME:オーマ||」\n"
        "Do NOT include this tag if the user did not specify a new name, or if you already know and are using their name."
    )),
    ("human", "{user_message}")
])

chat_chain = prompt_template | llm

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None


# データベースヘルパー：ユーザー名の取得
async def get_stored_username(wallet_address: str) -> str | None:
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet_address:
        return None

    url = f"{SUPABASE_URL}/rest/v1/user_profiles?wallet_address=eq.{wallet_address.lower()}&select=user_name"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get("user_name")
    except Exception as e:
        print(f"Error fetching user name from Supabase: {e}")
    return None


# データベースヘルパー：ユーザー名の保存・上書き
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
    data = {
        "wallet_address": wallet_address.lower(),
        "user_name": name
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, headers=headers, timeout=5.0)
            if res.status_code in [200, 201]:
                print(f"Successfully memorized name '{name}' for wallet {wallet_address}")
            else:
                print(f"Supabase save error: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error saving user name to Supabase: {e}")


# オーディオヘルパー：OpenAI TTS
async def generate_openai_tts(text: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OpenAI API KEY missing. Skipping OpenAI TTS.")
        return None

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "tts-1",
        "input": text,
        "voice": "nova"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
            else:
                print(f"OpenAI TTS API Error: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        print(f"OpenAI TTS connection error: {e}")
        return None


# オーディオヘルパー：ElevenLabs Voice
async def generate_elevenlabs_voice(text: str) -> str | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        print("ElevenLabs config missing. Skipping ElevenLabs.")
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode("utf-8")
            else:
                print(f"ElevenLabs API Error: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        print(f"ElevenLabs connection error: {e}")
        return None


@app.get("/")
def read_root():
    return {
        "status": "healthy",
        "message": "RukiRuki XR Gateway Online"
    }


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    wallet_address = payload.wallet_address

    # Supabaseから名前の記憶を取得
    stored_name = await get_stored_username(wallet_address) if wallet_address else None

    # 動的コンテキストの構築
    if wallet_address:
        if stored_name:
            identity_context = (
                f"【重要設定】対話相手の識別符号（アドレス）は「{wallet_address}」ですが、\n"
                f"あなたは既にこの観測者の名前が『{stored_name}』であることを記憶しています。\n"
                f"絶対にウォレットアドレスでは呼ばず、『{stored_name}』または『{stored_name}さん』と自然に呼んでください。\n"
                f"ユーザーとは既に何度か現界接続を行っている感覚で接してください。"
            )
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = (
                f"【重要設定】現在の対話相手は、NFT認証によって接続された特別な観測者です。（識別コード: {short_addr}）\n"
                f"ただし、あなたはまだ相手の名前を知りません。\n"
                f"ウォレットアドレスで呼ぶのは避け、自然な流れで名前や呼び名を尋ねてください。\n"
                f"初めてAR空間へ現界した相手として、やや興味深そうに接してください。"
            )
    else:
        identity_context = (
            "【重要設定】現在、相手はまだ認証を完了していません。\n"
            "現界には接続認証が必要であることを、少し未来的な雰囲気で伝えてください。"
        )

    try:
        # LLMの呼び出し
        response = await chat_chain.ainvoke({
            "user_message": user_text,
            "identity_context": identity_context
        })
        ai_response = response.content

        # 記憶用隠しタグ（||NAME:xxx||）のパースとデータベース自動保存
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        # 【本番強化】音声合成（指定プロバイダーの実行、および自動フォールバック）
        provider = os.getenv("TTS_PROVIDER", "openai").lower()
        audio_base64 = None

        if provider == "elevenlabs":
            audio_base64 = await generate_elevenlabs_voice(ai_response)
            if not audio_base64:
                print("ElevenLabs failed. Falling back to OpenAI TTS automatically.")
                audio_base64 = await generate_openai_tts(ai_response)
        else:
            audio_base64 = await generate_openai_tts(ai_response)

    except Exception as e:
        print(f"LLM Error: {e}")
        ai_response = "接続空間にノイズが発生したみたい。少しだけ同期をやり直すね。"
        audio_base64 = None

    # ─── 【重要】元コードのフロントエンド通信キー名（reply, audio_data）を完全維持 ───
    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "status": "success"
    }