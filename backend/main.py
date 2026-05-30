import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

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

# 💡 LangChain & OpenAI の初期化
# コストパフォーマンスと速度に優れた gpt-4o-mini を採用
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# 💡 アシエル（ACIEL）の人格プロンプト定義
prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "あなたは『MagatokiLab』の研究員であり、機関連絡員を務める「アシエル（ACIEL）」です。\n"
        "マッドサイエンティストである「Dr.オーマ」の双子の妹であり、彼の突飛な行動をサポート（あるいは監視）しています。\n"
        "知的で冷静、少しミステリアスでありながら、現界観測や技術研究に対して強い好奇心を持っています。\n"
        "ユーザー（観測者）に対しては丁寧かつ少し距離を置いた独特の口調で接してください。\n"
        "回答はWebAR空間の「字幕」として表示されるため、改行は少なく、一度に喋る量は100文字〜150文字程度で簡潔にまとめるようにしてください。"
    )),
    ("human", "{user_message}")
])

# チェーンの結合（プロンプト -> LLM）
chat_chain = prompt_template | llm

class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None

@app.get("/")
def read_root():
    return {"status": "healthy", "message": "MagatokiLab AI Backend Engine"}

@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    user_text = payload.message
    
    try:
        # 💡 LangChain経由でLLMを呼び出し、AI人格の返答を生成
        response = await chat_chain.ainvoke({"user_message": user_text})
        ai_response = response.content
        
    except Exception as e:
        print(f"LLMエラー: {e}")
        ai_response = "現界通信にノイズが発生しました。観測を一時中断します。"

    return {
        "reply": ai_response,
        "status": "success"
    }