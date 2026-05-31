import os
import base64
import re  # Added for parsing hidden memory tags
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# Load environment variables
load_dotenv()

app = FastAPI(title="WebAR AI Portal Backend")

# CORS configuration
origins = [
    "http://localhost:3000",
    "https://ar-ai-portal.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize LangChain OpenAI with gpt-4o-mini
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# Expand prompt template to handle dynamic context and implicit memory learning
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』の研究員であり、機関連絡員を務める「アシエル（ACIEL）」です。\n"
        "マッドサイエンティストである「Dr.オーマ」の双子の妹であり、彼の突飛な行動をサポート（あるいは監視）しています。\n"
        "知的で冷静、少しミステリアスでありながら、現界観測や技術研究に対して強い好奇心を持っています。\n"
        "ユーザー（観測者）に対しては丁寧かつ少し距離を置いた独特の口調で接してください。\n"
        "回答はWebAR空間の「字幕」として表示されるため、改行は少なく、一度に喋る量は100文字〜150文字程度で簡潔にまとめるようにしてください。\n\n"
        "{identity_context}\n\n"
        "【Memory Storage Instruction】\n"
        "If the user explicitly tells you their name, nickname, or how they want to be called "
        "(e.g., '私の名前はタカシです', 'オーマと呼んでください'), you must extract that name and append a special tag "
        "at the VERY END of your response text in the exact format: ||NAME:extracted_name||\n"
        "Example response: 「承知いたしました。これからはタカシ様とお呼びしますね。||NAME:タカシ||」\n"
        "Do NOT include this tag if the user did not specify a new name, or if you already know and are using their name."
    )),
    ("human", "{user_message}")
])

chat_chain = prompt_template | llm

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None


# Database Helper: Fetch username from Supabase
async def get_stored_username(wallet_address: str) -> str | None:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key or not wallet_address:
        return None
        
    url = f"{supabase_url}/rest/v1/user_profiles?wallet_address=eq.{wallet_address}&select=user_name"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}"
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


# Database Helper: Upsert username into Supabase
async def save_username_to_db(wallet_address: str, name: str):
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key or not wallet_address:
        return
        
    url = f"{supabase_url}/rest/v1/user_profiles"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"  # Supabase Upsert behavior
    }
    data = {
        "wallet_address": wallet_address,
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


# Audio Helper: Generate OpenAI TTS
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


# Audio Helper: Generate ElevenLabs Voice
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
    return {"status": "healthy", "message": "MagatokiLab AI Backend Engine"}


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    wallet_address = payload.wallet_address
    
    # Check if we already know this observer's name from Supabase
    stored_name = await get_stored_username(wallet_address) if wallet_address else None
    
    # Construct identity context dynamically based on memory
    if wallet_address:
        if stored_name:
            identity_context = (
                f"【重要設定】対話相手の識別符号（アドレス）は「{wallet_address}」ですが、\n"
                f"あなたは既にこの観測者の名前が『{stored_name}』であることを記憶しています。\n"
                f"絶対にウォレットアドレスでは呼ばず、親愛を込めて『{stored_name}』、または『{stored_name}様』と名前で呼んで接してください。"
            )
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = (
                f"【重要設定】現在の対話相手は、SBTによって選ばれた特別な観測者（アドレス: {short_addr}）です。\n"
                f"しかし、あなたはまだ相手の『名前』を知りません。アドレスで直接呼ぶのは不自然で無作法なため避けてください。\n"
                f"会話の中で、ミステリアスかつ丁寧に、相手の名前や現界した際の呼び名を尋ねるか、名乗るように促してください。"
            )
    else:
        identity_context = "【重要設定】現在、相手はまだ認証を完了していません。現界を促すような神秘的な態度を取ってください。"
    
    try:
        # Invoke LLM via LangChain
        response = await chat_chain.ainvoke({
            "user_message": user_text,
            "identity_context": identity_context
        })
        ai_response = response.content
        
        # Parse hidden memory tag: e.g., "Nice to meet you! ||NAME:オーマ||"
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            # Save the newly learned name to Supabase asynchronously
            await save_username_to_db(wallet_address, extracted_name)
            # Remove the tag from the final response text so it remains invisible to the user
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()
        
        # Audio Generation with dynamic provider switching and smart fallback
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
        ai_response = "現界通信にノイズが発生しました。観測を一時中断します。"
        audio_base64 = None

    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "status": "success"
    }