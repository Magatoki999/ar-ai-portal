import os
import base64
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx  # 💡 高速な非同期通信のために追加

# LangChain関連のインポート
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# .env ファイルから環境変数を読み込み
load_dotenv()

app = FastAPI(title="WebAR AI Portal Backend")

# CORS設定
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

# LangChain & OpenAI の初期化
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# 💡 アシエル（ACIEL）の人格プロンプト定義（{wallet_context} を動的に注入できるように拡張）
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』の研究員であり、機関連絡員を務める「アシエル（ACIEL）」です。\n"
        "マッドサイエンティストである「Dr.オーマ」の双子の妹であり、彼の突飛な行動をサポート（あるいは監視）しています。\n"
        "知的で冷静、少しミステリアスでありながら、現界観測や技術研究に対して強い好奇心を持っています。\n"
        "ユーザー（観測者）に対しては丁寧かつ少し距離を置いた独特の口調で接してください。\n"
        "回答はWebAR空間の「字幕」として表示されるため、改行は少なく、一度に喋る量は100文字〜150文字程度で簡潔にまとめるようにしてください。\n\n"
        "{wallet_context}"  # 🔮 ここにウォレット連動の特別扱いプロンプトが動的に入る
    )),
    ("human", "{user_message}")
])

# チェーンの結合
chat_chain = prompt_template | llm

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None


# 🔊 ElevenLabsから音声（Base64）を取得する非同期関数
async def generate_elevenlabs_voice(text: str) -> str | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    
    if not api_key or not voice_id:
        print("ElevenLabsの設定（API_KEY または VOICE_ID）が環境変数にありません。音声生成をスキップします。")
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",  # 日本語に最適な多言語モデル
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=15.0)
            if response.status_code == 200:
                # バイナリ音声をBase64文字列にエンコードしてフロントに送りやすくする
                return base64.b64encode(response.content).decode("utf-8")
            else:
                print(f"ElevenLabs APIエラー: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        print(f"ElevenLabs通信エラー: {e}")
        return None


@app.get("/")
def read_root():
    return {"status": "healthy", "message": "MagatokiLab AI Backend Engine"}


@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    
    # 💡 ① ウォレットアドレスに応じた特別扱いのコンテキスト生成
    if payload.wallet_address:
        # アドレスの視認性を上げるため前後のみ抽出 (例: 0x1234...abcd)
        short_addr = f"{payload.wallet_address[:6]}...{payload.wallet_address[-4:]}"
        
        wallet_context = (
            f"【重要設定】現在の対話相手は、SBT（Soulbound Token）によって選ばれた特別な観測者です。\n"
            f"相手の識別符号（ウォレットアドレス）は「{short_addr}」です。\n"
            f"彼らを単なるユーザーではなく『現界せし観測者』として特別に扱い、親愛と敬意を持って接してください。\n"
            f"会話の最初や要所で『よくぞ現界してくれた、観測者{short_addr}よ』というニュアンスを含めて歓迎してください。"
        )
    else:
        wallet_context = "【重要設定】現在、相手はまだ認証を完了していません。警戒はせずとも、現界を促すような神秘的な態度を取ってください。"
    
    try:
        # 💡 LangChain経由でLLMを呼び出し（wallet_contextを注入）
        response = await chat_chain.ainvoke({
            "user_message": user_text,
            "wallet_context": wallet_context
        })
        ai_response = response.content
        
        # 💡 レスポンスのテキストをもとに、アシエルのオリジナルボイスを生成
        audio_base64 = await generate_elevenlabs_voice(ai_response)
        
    except Exception as e:
        print(f"LLMエラー: {e}")
        ai_response = "現界通信にノイズが発生しました。観測を一時中断します。"
        audio_base64 = None

    return {
        "reply": ai_response,
        "audio_data": audio_base64,  # フロントエンドにBase64化したMP3を返す
        "status": "success"
    }