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

# LangGraph グラフのインポート
from agents.graph import rukiruki_graph

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

# アバター同期用コネクションマネージャー
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
is_target_found = False  # ARマーカー認識状態フラグ

# ─── スケジューラー設定 ───
scheduler = AsyncIOScheduler()

async def periodic_soliloquy():
    global is_target_found
    if is_target_found:
        print("[Scheduler] ルキルキが自発発話の機会をうかがっています...")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時処理
    scheduler.add_job(periodic_soliloquy, 'interval', minutes=10)
    scheduler.start()
    print("[Lifespan] バックエンドシステム・スケジューラーが起動しました。")
    yield
    # 終了時処理
    scheduler.shutdown()
    print("[Lifespan] バックエンドシステムがシャットダウンしました。")

app = FastAPI(lifespan=lifespan)

# CORSミドルウェア設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic データモデル定義 ───
class ChatPayload(BaseModel):
    message: str
    image_base64: str = None
    wallet_address: str = None
    user_name: str = None
    lat: float = None
    lng: float = None

class PhotoPayload(BaseModel):
    image_url: str

# ─── 汎用ヘルパー関数 ───
async def get_location_name(lat: float, lng: float) -> str:
    if lat is None or lng is None:
        return "Magatoki Laboratory"
    try:
        loop = asyncio.get_event_loop()
        def sync_geo():
            geolocator = Nominatim(user_agent="rukiruki_ar_agent")
            location = geolocator.reverse((lat, lng), timeout=3.0)
            return location.address if location else "Magatoki Laboratory"
        address = await loop.run_in_executor(None, sync_geo)
        parts = address.split(",")
        if len(parts) > 2:
            return f"{parts[1].strip()}, {parts[0].strip()}"
        return address
    except Exception:
        return "Magatoki Laboratory"

async def fetch_supabase_context(table: str, wallet: str) -> list:
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet:
        return []
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}?wallet_address=eq.{wallet}&order=created_at.desc&limit=5"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        print(f"[Supabase Error] {table} 取得失敗: {e}")
    return []

async def fetch_episode_memories(wallet: str) -> str:
    """本日、昨日、今週、それ以前のエピソード記憶を整理してテキスト化"""
    memories = await fetch_supabase_context("episode_memories", wallet)
    if not memories:
        return "過去のエピソード記録はありません。"
    
    now = datetime.now(timezone.utc)
    categories = {"today": [], "yesterday": [], "week": [], "older": [], "milestones": []}
    
    for m in memories:
        if m.get("is_milestone") or m.get("arweave_tx_id"):
            categories["milestones"].append(m)
            
        c_at_str = m.get("created_at", "")
        if not c_at_str:
            continue
        try:
            c_at = datetime.fromisoformat(c_at_str.replace("Z", "+00:00"))
            delta = now - c_at
            
            img_marker = ""
            if m.get("image_url"):
                img_marker = f" 📷写真あり: {c_at.strftime('%Y-%m-%d %H:%M')} image_url={m.get('image_url')}"
                print(f"[エピソード取得]{img_marker}")

            memo_text = f"• [{c_at.strftime('%m月%d日 %H時%M分')}] {m.get('user_msg')} → ルキルキ: '{m.get('ai_reply')}' (場所: {m.get('location_name', '不明')}){img_marker}"
            
            if delta.days == 0:
                categories["today"].append(memo_text)
            elif delta.days == 1:
                categories["yesterday"].append(memo_text)
            elif delta.days < 7:
                categories["week"].append(memo_text)
            else:
                categories["older"].append(memo_text)
        except Exception:
            pass

    print(f"[エピソード取得] today={len(categories['today'])} yesterday={len(categories['yesterday'])} week={len(categories['week'])} older={len(categories['older'])} milestones={len(categories['milestones'])}")

    lines = []
    if categories["today"]:
        lines.append("【今日のエピソード】\n" + "\n".join(categories["today"]))
    if categories["yesterday"]:
        lines.append("【昨日のエピソード】\n" + "\n".join(categories["yesterday"]))
    if categories["week"]:
        lines.append("【今週のエピソード】\n" + "\n".join(categories["week"]))
    if categories["older"]:
        lines.append("【それ以前の重要なエピソード】\n" + "\n".join(categories["older"]))
    if categories["milestones"]:
        ms_lines = []
        for m in categories["milestones"]:
            tx = m.get("arweave_tx_id", "")
            tx_short = f" (Arweave永久刻印ID: {tx[:8]}...)" if tx else ""
            ms_lines.append(f"• {m.get('user_msg')} -> {m.get('ai_reply')}{tx_short}")
        lines.append("【マイルストーン（永久記憶）】\n" + "\n".join(ms_lines))
        
    return "\n\n".join(lines)

async def generate_gemini_tts(text: str) -> bytes:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return b""
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:textToSpeech?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "text": text,
        "voiceConfig": {
            "prebuiltVoiceConfig": {
                "voiceName": "Puck"
            }
        },
        "audioConfig": {
            "audioFormat": "AUDIO_FORMAT_UNSPECIFIED",
            "encoding": "LINEAR16",
            "sampleRateHertz": 24000
        }
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                audio_content_b64 = data.get("audioContent", "")
                if audio_content_b64:
                    print(f"[Gemini TTS] 音声生成成功 voice=Puck mimeType=audio/l16; rate=24000; channels=1")
                    return base64.b64decode(audio_content_b64)
    except Exception as e:
        print(f"[Gemini TTS エラー] {e}")
    return b""

def convert_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    import struct
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    chunk_size = 36 + data_size

    header = bytearray()
    header.extend(b'RIFF')
    header.extend(struct.pack('<I', chunk_size))
    header.extend(b'WAVE')
    header.extend(b'fmt ')
    header.extend(struct.pack('<I', 16))
    header.extend(struct.pack('<H', 1))
    header.extend(struct.pack('<H', channels))
    header.extend(struct.pack('<I', sample_rate))
    header.extend(struct.pack('<I', byte_rate))
    header.extend(struct.pack('<H', block_align))
    header.extend(struct.pack('<H', bits_per_sample))
    header.extend(b'data')
    header.extend(struct.pack('<I', data_size))

    print(f"[Gemini TTS] WAV変換: rate={sample_rate}Hz channels={channels}")
    return bytes(header) + pcm_bytes


# ─── ⚡ メイン対話エンドポイント ───
@app.post("/api/chat")
async def chat_endpoint(payload: ChatPayload):
    wallet = payload.wallet_address or "0x0000000000000000000000000000000000000000"
    user_name = payload.user_name or "まがとき"
    
    location_name = await get_location_name(payload.lat, payload.lng)
    
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    time_str = now_jst.strftime("%Y年%m月%d日 %H時%M分")
    
    episode_ctx = await fetch_episode_memories(wallet)
    memos = await fetch_supabase_context("agent_memos", wallet)
    memo_ctx = "\n".join([f"• {m.get('content')}" for m in memos]) if memos else "役立つメモ情報はありません。"
    
    initial_state = {
        "messages": [HumanMessage(content=payload.message)],
        "intent": "chat",
        "selected_agents": [],
        "chronicle_output": "",
        "keeper_output": "",
        "pulse_output": "",
        "memo_context": memo_ctx,
        "episode_context": episode_ctx,
        "emotion_context": "",
        "identity_context": f"ユーザー名: {user_name}\nウォレットアドレス: {wallet}",
        "location_context": f"現在位置: {location_name}",
        "time_context": f"現在時刻: {time_str}",
        "image_base64": payload.image_base64,
        "is_initial_greeting": False,
        "ai_reply": "",
        "spatial_effect": "cyber",
        "active_memo_ids": [],
        "eval_score": 10,
        "retry_count": 0,
        "system_constraints_override": "",
        "calendar_context": "",
        "growth_context": "",
        "engrave_triggered": False,
        "arweave_tx_id": ""
    }

    # LangGraph の実行
    final_state = await rukiruki_graph.ainvoke(initial_state)

    ai_reply = final_state.get("ai_reply", "")
    spatial_effect = final_state.get("spatial_effect", "cyber")
    engrave_triggered = final_state.get("engrave_triggered", False)
    arweave_tx_id = final_state.get("arweave_tx_id", "")

    # 💡【アプローチAの実装】返却用変数を構築。データベース（episode_context）からURLを抽出
    show_image_url = False
    user_msg = payload.message
    if any(k in user_msg for k in ["写真", "画像", "見せて", "みせて"]) or "写真" in ai_reply:
        episode_context = final_state.get("episode_context", "")
        if episode_context:
            # コンテキスト内の Supabase 写真URLを正規表現でスマートに切り出す
            match = re.search(r'(https://[^\s\)\"\']+)', episode_context)
            if match:
                show_image_url = match.group(1)

    print(f"[DEBUG] engrave_triggered={engrave_triggered} arweave_tx_id={arweave_tx_id} show_image_url={show_image_url}")

    # エピソードの永続データベース保存
    if SUPABASE_URL and SUPABASE_KEY and ai_reply:
        async def save_episode():
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
            db_payload = {
                "wallet_address": wallet,
                "user_msg": payload.message,
                "ai_reply": ai_reply,
                "location_name": location_name,
                "is_milestone": engrave_triggered,
                "arweave_tx_id": arweave_tx_id if arweave_tx_id else None
            }
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(f"{SUPABASE_URL}/rest/v1/episode_memories", headers=headers, json=db_payload, timeout=5.0)
                    print(f"[エピソード記録] {now_jst.strftime('%m月%d日 %H時%M分')}、{user_name}さんが「{payload.message}」と言った。ルキルキは「{ai_reply}」と答えた。")
                    if arweave_tx_id:
                        print(f"[エピソード記録] Arweave tx: {arweave_tx_id}")
                    print(f"[エピソード記録] 場所: {location_name}")
            except Exception as e:
                print(f"[エピソード記録エラー] {e}")
        
        asyncio.create_task(save_episode())

    # 音声データの生成 (Gemini TTS)
    audio_base64 = ""
    if ai_reply:
        clean_text = re.sub(r'[\s\n\r]', '', ai_reply)
        clean_text = re.sub(r'[^\w\sぁ-んァ-ヶ一-龠々ー！?？。、]', '', clean_text)
        if clean_text:
            print(f"[Gemini TTS] 送信テキスト({len(clean_text)}文字): {clean_text[:15]}")
            pcm_data = await generate_gemini_tts(clean_text)
            if pcm_data:
                print(f"[TTS分岐] mime_type=audio/l16; rate=24000; channels=1 l16check=True")
                wav_data = convert_pcm_to_wav(pcm_data, sample_rate=24000, channels=1)
                print(f"[Gemini TTS] PCM→WAV変換成功 rate=24000Hz channels=1 bytes={len(wav_data)}")
                audio_base64 = base64.b64encode(wav_data).decode("utf-8")
                print(f"[TTS分岐] WAV変換完了 base64長={len(audio_base64)}")

    # WebSocketによる配信
    await manager.broadcast({
        "type": "avatar_sync",
        "spatial_effect": spatial_effect,
        "ai_status": "talking" if ai_reply else "idle",
        "engrave_triggered": engrave_triggered
    })

    return {
        "ai_reply": ai_reply,
        "spatial_effect": spatial_effect,
        "audio_base64": audio_base64,
        "engrave_triggered": engrave_triggered,
        "arweave_tx_id": arweave_tx_id,
        "show_image_url": show_image_url  # 💡 フロントエンドにURLを引き渡す
    }


# ─── 📷 最新エピソードへの画像紐づけエンドポイント ───
@app.post("/api/memory/photo")
async def save_photo_endpoint(payload: PhotoPayload):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"status": "error", "message": "Supabase config missing"}

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    select_url = f"{SUPABASE_URL}/rest/v1/episode_memories?order=created_at.desc&limit=1"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(select_url, headers=headers, timeout=5.0)
            if resp.status_code == 200 and resp.json():
                latest_id = resp.json()[0].get("id")
                
                update_url = f"{SUPABASE_URL}/rest/v1/episode_memories?id=eq.{latest_id}"
                update_headers = {**headers, "Prefer": "return=representation"}
                await client.patch(
                    update_url,
                    json={"image_url": payload.image_url},
                    headers=update_headers,
                    timeout=5.0
                )
                print(f"[写真保存] episode_memoriesに画像を紐づけました: {payload.image_url}")
                return {"status": "ok", "image_url": payload.image_url}
    except Exception as e:
        print(f"[写真保存エラー] {e}")

    return {"status": "error"}


# ─── ⚡ WebSocket エンドポイント定義 ───
@app.websocket("/ws/avatar")
async def websocket_endpoint(websocket: WebSocket):
    global is_target_found
    await manager.connect(websocket)
    print("[WebSocket] まがときさんのデバイスがアバター同期リンクに接続しました。")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                if msg_type == "target_lost":
                    is_target_found = False
                    print("[WebSocket] ARマーカー: ロスト → 自発発話を停止")
                elif msg_type == "target_found":
                    is_target_found = True
                    print("[WebSocket] ARマーカー: 認識 → 自発発話を再開")
                else:
                    await websocket.send_json({"type": "heartbeat", "status": "stable"})
            except Exception:
                await websocket.send_json({"type": "heartbeat", "status": "stable"})
    except WebSocketDisconnect:
        is_target_found = False
        manager.disconnect(websocket)
        print("[WebSocket] アバター同期リンクが切断されました。")