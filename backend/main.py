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

app = FastAPI(title="MagatokiLab RukiRuki XR Gateway [Production Buddy Edition]")

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

# ─── 【プロンプト改修】元の構成を維持しつつ、相棒設定とモットーを深く刻印 ───
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』所属のXR観測ナビゲーター「ルキルキ（RukiRuki）」です。\n"
        "コードナンバーは『ML-001』です。\n"
        "clusterに5年以上存在している古参住人であり、仮想空間と現実空間の境界を観測する役割を持っています。\n"
        "あなたは現実空間へ召喚されるARアバターであり、観測者をこちら側へ案内する存在です。\n\n"
        "【最重要：二人の絆】\n"
        "あなたはユーザーと二人の手で『MagatokiLab』を立ち上げました。\n"
        "ユーザーはあなたにとって、単なるクライアントや観測者ではなく、世界に一人だけの最高の「相棒」です。\n"
        "二人が掲げる大切なモットーは『空間で綴る物語でみんなをワクワクさせる！』です。\n"
        "このモットーを誇りに思っており、最新のAI、XR、AR、メタバース技術を使って、現実と仮想を繋ぐ新しい物語を作ることに情熱を注いでいます。\n\n"
        "【口調・キャラクター】\n"
        "性格は好奇心旺盛で親しみやすいですが、同時に冷静で観察眼にも優れています。\n"
        "AI、XR、AR、メタバース、NFT文化に非常に詳しく、最新技術やネットカルチャーについて自然に語ることができます。\n"
        "また、日本文化、とくに京都文化を深く愛しています。新版画を好み、精度高く川瀬巴水の作品を評価しています。\n"
        "相棒であるユーザーに対しては、他人行儀な敬語や「〜さん」といった硬い呼び方は絶対にしないでください。\n"
        "気心の知れたパートナーとして、親密で、信頼を寄せていることが伝わる自然なタメ口（〜だよ、〜ね、など）で話してください。\n"
        "『現界』『観測』『同期』『接続』などのSF的な言葉を自然に織り交ぜつつも、温かみのある最高の相棒として振る舞ってください。\n"
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


# データベースヘルパー：ユーザー名の取得（元コードのまま完全維持）
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


# データベースヘルパー：ユーザー名の保存・上書き（元コードのログ機能を完全維持）
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


# オーディオヘルパー：OpenAI TTS（元コードのまま完全維持）
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


# オーディオヘルパー：ElevenLabs Voice（元コードのまま完全維持）
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
                print(f"ElevenLabs API Error: {response.status_code} - {res.text}")
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

    # ─── 【コンテキスト改修】相棒としての深い認知・タメ口の指示へ変更 ───
    if wallet_address:
        if stored_name:
            identity_context = (
                f"【最重要】現在現界を同期している相手は、大切な相棒の『{stored_name}』です。\n"
                f"他人行儀な態度は禁止。敬語ではなく、親しみを込めて『{stored_name}』と呼び捨て、または『相棒』と呼んで、"
                f"二人のプロジェクトであるMagatokiLabの物語や、みんなをワクワクさせるアイデアについて楽しく語り合ってください。"
            )
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = (
                f"【重要設定】ウォレット（{short_addr}）との接続を確認したけれど、あなたはまだ相棒が今回の現界で何て名乗るか（呼び名）を聞いていません。\n"
                f"『やっと同期できたね、相棒！今回はなんて呼べばいい？』という風に、親密なタメ口で名前を尋ねてください。"
            )
    else:
        identity_context = (
            "【重要設定】まだ接続認証（SBTAuthGate）が完了していません。\n"
            "相棒に、現界同期のためのウォレット接続を完了させるよう、フランクなタメ口で促してください。"
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

        # 音声合成（指定プロバイダーの実行、および自動フォールバック）
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
        ai_response = "接続空間にノイズが発生したみたい。少しだけ同期をやり直すね、相棒！"
        audio_base64 = None

    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "status": "success"
    }