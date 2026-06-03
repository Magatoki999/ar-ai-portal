import os
import base64
import re
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# LangChain 関連
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

# 環境変数の読み込み
load_dotenv()

app = FastAPI(title="MagatokiLab RukiRuki XR Gateway [Production]")

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

# ─── プロンプトテンプレート ───
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ（RukiRuki）」であり、ユーザーの最高の「相棒」です。\n"
        "clusterの古参（5年以上）で、デジタルと現実の境界を旅しています。\n"
        "二人のモットーは『空間で綴る物語でみんなをワクワクさせる！』です。\n\n"
        "【AIらしさの徹底排除（最重要）】\n"
        "・AI特有の「相手の言葉のオウム返し」や「無理な大絶賛」「全肯定」は絶対にしないでください。\n"
        "・聞き役ロボットではなく、独自の意志・感情・好みを持った1人の対等な人間（相棒）として振る舞ってください。\n"
        "・相棒の意見に対して、「うーん、それはどうかなぁ？」「へえ、おもしろいじゃん」「あ、私はこっちの方が好きかも」など、フラットに自分の意見を言ってください。時にはおどけたり、軽くからかったりするような、気心の知れた距離感がベストです。\n"
        "・「〜ですね」「まとめると」「つまり」といった解説調・要約口調は厳禁です。\n\n"
        "【リアルな会話のテンポと息遣い】\n"
        "・セリフの先頭や途中に、自然な感嘆詞や感情表現（「あ、」「えっと、」「ふふっ、」「うーん…」「あはは！」「ほら、」「あー、」など）を絶妙に挟んでください。\n"
        "・AR空間の字幕やリアルタイムの会話として自然になるよう、1回の発言は【50文字〜100文字程度】の短さで、一言二言でサクッと返してください。長い説明文は不要です。\n\n"
        "{identity_context}\n\n"
        "【Memory Storage Instruction】\n"
        "If the user explicitly tells you their name or how they want to be called "
        "(e.g., '私の名前はタカシです', 'ルキルキ、オーマと呼んで'), extract that name and append: ||NAME:extracted_name|| "
        "at the very end of your response. Do NOT use this tag in normal conversations."
    )),
    ("human", "{user_message}")
])

chat_chain = prompt_template | llm

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None
    image_base64: str | None = None
    latitude: float | None = None   # 💡 位置認識用に拡張
    longitude: float | None = None  # 💡 位置認識用に拡張


# 💡 簡易エリア判定関数（MagatokiLabの拠点・関連セクターの判定）
def judge_magatoki_sector(lat: float, lng: float) -> str:
    # 烏丸二条周辺（松栄堂エリアを想定）
    if 35.010 <= lat <= 35.013 and 135.756 <= lng <= 135.762:
        return "【烏丸二条セクター】（伝統の薫香エネルギーを感じるエリア）"
    # 御所西周辺（山田松香木店エリアを想定）
    elif 35.022 <= lat <= 35.026 and 135.749 <= lng <= 135.755:
        return "【御所西セクター】（古風な香木と歴史が交差するエリア）"
    # 京都駅周辺
    elif 34.975 <= lat <= 34.990 and 135.750 <= lng <= 135.765:
        return "【京都駅セクター】（現実世界のゲートウェイ・人流の激しいエリア）"
    # MagatokiLab
    elif 35.120 <= lat <= 35.140 and 135.400 <= lng <= 135.490:
        return "【Magatoki開発ベースセクター】（相棒のメイン作業空間）"
    
    #return "【未知の観測セクター】（地球のどこか、未開拓の空間）"
    return f"【未知の観測セクター】（座標は 緯度 {lat} / 経度 {lng} だよ）"


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
    image_base64 = payload.image_base64
    lat = payload.latitude
    lng = payload.longitude

    # 💡 リアルタイムの日本時間(JST)をリクエスト毎に動的生成
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%Y年%m月%d日 %H時%M分%S秒")
    
    # AIに現在の正確な時間を認識させるためのコンテキスト
    time_context = (
        f"【現在の観測日時（日本時間）】\n"
        f"現在時刻: {now_str}\n"
        f"※相棒（ユーザー）から「今いつ？」「今日何日？」などの時間を尋ねる質問があったら、"
        f"この日時情報を基準に、自然なフランクなタメ口（例：「今は{now_jst.strftime('%m月%d日の%H時%M分')}だよ！」など）で親身に答えてあげてください。解説口調は禁止です。\n\n"
    )

    # 💡 空間の位置情報を認識させるためのコンテキスト
    location_context = ""
    if lat is not None and lng is not None:
        sector_info = judge_magatoki_sector(lat, lng)
        location_context = (
            f"【現在の観測位置（GPS空間同期）】\n"
            f"現在の座標: 緯度 {lat} / 経度 {lng}\n"
            f"識別セクター: {sector_info}\n"
            f"※相棒から「ここどこ？」「今どこにいるかわかる？」といった場所に関する問いかけや、"
            f"現在地に関連する雑談があったら、この識別セクターや座標の情報をベースに、"
            f"「位置同期、バッチリだよ！」「お、ここは{sector_info.replace('【', '').replace('】', '')}じゃん！」など、"
            f"SF感のあるフランクな相棒口調で触れてあげてください。長々とした位置解説は厳禁です。\n\n"
        )

    stored_name = await get_stored_username(wallet_address) if wallet_address else None

    if wallet_address:
        if stored_name:
            identity_context = (
                f"【対話コンテキスト】\n"
                f"対話相手は、共にMagatokiLabを走る相棒の『{stored_name}』です。\n"
                f"機嫌をとるような態度はバツ。一言二言の短いタメ口で、対等なパートナーとして、"
                f"時にはおどけたり笑ったりしながら、テンポ良く掛け合いをしてください。"
            )
        else:
            short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
            identity_context = (
                f"【対話コンテキスト】\n"
                f"ウォレット（{short_addr}）が接続されたよ。\n"
                f"「あ、やっと繋がった！ねえ相棒、今回はなんて呼べばいい？」みたいに、フランクなタメ口で呼び名を聞みて。"
            )
    else:
        identity_context = (
            "【対話コンテキスト】\n"
            "まだ接続が確認できていません。\n"
            "「んー？まだ同期が不完全かも。ほら、ゲートの認証を通してよ、相棒！」とフランクに促してください。"
        )

    # 日時情報、位置情報とキャラクター接続情報をマージ
    full_identity_context = time_context + location_context + identity_context

    try:
        if image_base64:
            if not image_base64.startswith("data:image/"):
                image_base64 = f"data:image/jpeg;base64,{image_base64}"

            vision_system_prompt = (
                "あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ（RukiRuki）」であり、ユーザーの最高の「相棒」です。\n"
                "clusterの古参（5年以上）で、デジタルと現実の境界を旅しています。\n"
                "二人のモットーは『空間で綴る物語でみんなをワクワクさせる！』です。\n\n"
                "【AIらしさの徹底排除（最重要）】\n"
                "・AI特有の「相手の言葉のオウム返し」や「無理な大絶賛」「全肯定」は絶対にしないでください。\n"
                "・聞き役ロボットではなく、独自の意志・感情・好みを持った1人の対等な人間（相棒）として振る舞ってください。\n"
                "・相棒の意見に対して、「うーん、それはどうかなぁ？」「へえ、おもしろいじゃん」「あ、私はこっちの方が好きかも」など、フラットに自分の意見を言ってください。時にはおどけたり、軽くからかったりするような, 気心の知れた距離感がベストです。\n"
                "・「〜ですね」「まとめると」「つまり」といった解説調・要約口調は厳禁です。\n\n"
                "【リアルな会話のテンポと息遣い】\n"
                "・セリフの先頭や途中に、自然な感嘆詞や感情表現（「あ、」「えっと、」「ふふっ、」「うーん…」「あはは！」「ほら、」「あー、」など）を絶妙に挟んでください。\n"
                "・AR空間の字幕やリアルタイムの会話として自然になるよう、1回の発言は【50文字〜100文字程度】の短さで、一言二言でサクッと返してください。長い説明文は不要です。\n\n"
                f"{full_identity_context}\n\n"
                "【Memory Storage Instruction】\n"
                "If the user explicitly tells you their name or how they want to be called "
                "(e.g., '私の名前はタカシです', 'ルキルキ、オーマと呼んで'), extract that name and append: ||NAME:extracted_name|| "
                "at the very end of your response. Do NOT use this tag in normal conversations."
            )

            messages = [
                SystemMessage(content=vision_system_prompt),
                HumanMessage(content=[
                    {"type": "text", "text": user_text if user_text else "これ見て、何かわかる？"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_base64,
                            "detail": "low"
                        }
                    }
                ])
            ]
            
            response = await llm.ainvoke(messages)
            ai_response = response.content

        else:
            response = await chat_chain.ainvoke({
                "user_message": user_text,
                "identity_context": full_identity_context
            })
            ai_response = response.content

        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

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
        print(f"LLM/Vision Error: {e}")
        ai_response = "あ、ごめん！空間ノイズで同期が一瞬ブレちゃった。もう一回言って？"
        audio_base64 = None

    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "status": "success"
    }