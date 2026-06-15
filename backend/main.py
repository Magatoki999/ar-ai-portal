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


def load_rukiruki_persona(user_call: str = "まがとき") -> str:
    """
    rukiruki_persona.md を読み込み、{USER_CALL} をユーザーの呼び名に置換して返す。
    user_call: DBから取得したpreferred_callまたはuser_name。未登録なら「まがとき」。
    """
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                raw = f.read()
            # {USER_CALL}をユーザーの呼び名に置換
            return raw.replace("{USER_CALL}", f"「{user_call}」")
        except Exception as e:
            pass
    return (
        f"あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        f"{user_call}さんの随伴AIとして、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
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

# ─── 京都行事カレンダー & 誕生日 ───
# 追加は {"月": 月, "日": 日, "name": "行事名", "days_before": 事前通知日数, "message": "ルキルキのメモ"} を追加するだけ
KYOTO_CALENDAR = [
    # ─ 誕生日 ─
    {"month": 4,  "day": 16, "name": "まがときさんの誕生日",       "days_before": 3, "message": "まがときさんの大切な日"},
    {"month": 7,  "day":  7, "name": "ルキルキの誕生日",           "days_before": 3, "message": "ルキルキ自身の誕生日"},
    # ─ 京都行事 ─
    {"month": 1,  "day":  1, "name": "初詣シーズン",               "days_before": 3, "message": "京都の神社に初詣の季節"},
    {"month": 2,  "day":  3, "name": "節分祭（吉田神社）",         "days_before": 3, "message": "吉田神社の節分祭、鬼やらい"},
    {"month": 3,  "day": 25, "name": "桜シーズン",                 "days_before": 3, "message": "京都の桜が見頃を迎える季節"},
    {"month": 5,  "day": 15, "name": "葵祭",                       "days_before": 3, "message": "京都三大祭のひとつ、葵祭"},
    {"month": 7,  "day":  1, "name": "祇園祭",                     "days_before": 3, "message": "京都の夏を彩る祇園祭が始まる"},
    {"month": 7,  "day": 17, "name": "祇園祭 山鉾巡行",            "days_before": 3, "message": "祇園祭のクライマックス、山鉾巡行"},
    {"month": 8,  "day": 16, "name": "五山送り火",                 "days_before": 3, "message": "お盆の締めくくり、五山に大文字が灯る"},
    {"month": 11, "day": 15, "name": "紅葉シーズン",               "days_before": 3, "message": "京都の紅葉が見頃を迎える季節"},
    {"month": 12, "day": 31, "name": "大晦日・除夜の鐘",           "days_before": 3, "message": "京都の寺院に除夜の鐘が響く"},
]


def get_calendar_context() -> str:
    """
    今日から3日以内に行事・誕生日があればコンテキストを生成する。
    当日と事前通知で異なるメッセージを返す。
    """
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    today = now_jst.date()
    lines = []

    for event in KYOTO_CALENDAR:
        # 今年の日付で計算
        try:
            event_date = today.replace(month=event["month"], day=event["day"])
        except ValueError:
            continue

        diff = (event_date - today).days

        # 過ぎていたら来年で計算
        if diff < 0:
            try:
                event_date = event_date.replace(year=today.year + 1)
                diff = (event_date - today).days
            except ValueError:
                continue

        days_before = event.get("days_before", 3)

        if diff == 0:
            # 当日
            if event["name"] == "まがときさんの誕生日":
                lines.append(f"🎂【今日はまがときさんの誕生日です！】心を込めてお祝いしてください。")
            elif event["name"] == "ルキルキの誕生日":
                lines.append(f"🎂【今日はルキルキ自身の誕生日です！】まがときさんに感謝を伝えてください。")
            else:
                lines.append(f"🌸【今日は{event['name']}です】{event['message']}。会話に自然に織り交ぜてください。")
        elif 0 < diff <= days_before:
            # 事前通知
            if event["name"] == "まがときさんの誕生日":
                lines.append(f"📅【{diff}日後にまがときさんの誕生日】さりげなく楽しみにしていることを伝えてもよいです。")
            elif event["name"] == "ルキルキの誕生日":
                lines.append(f"📅【{diff}日後にルキルキの誕生日】自分の誕生日が近いことをさりげなく触れてもよいです。")
            else:
                lines.append(f"📅【{diff}日後に{event['name']}】{event['message']}。近づいていることを自然に話題にしてください。")

    if not lines:
        return ""

    return (
        "【行事・特別な日】\n"
        + "\n".join(lines)
        + "\nこれらを押しつけがましくなく、会話の流れで自然に触れてください。\n\n"
    )


# ─── 成長カウンター ───
RUKIRUKI_BIRTH_DATE = datetime(2026, 7, 7, tzinfo=timezone.utc)   # ルキルキの誕生日
MAGATOKI_BIRTH_DATE = datetime(2026, 4, 16, tzinfo=timezone.utc)  # まがときさんの誕生日（年は無視して月日のみ使用）
SYSTEM_LAUNCH_DATE  = datetime(2026, 3, 1,  tzinfo=timezone.utc)  # システム稼働開始日（概算）


def get_growth_context() -> str:
    """
    ルキルキとまがときさんが一緒に過ごした日数・会話の蓄積感をコンテキストとして返す。
    """
    JST = timezone(timedelta(hours=+9))
    now_jst = datetime.now(JST)
    days_together = (now_jst.date() - SYSTEM_LAUNCH_DATE.astimezone(JST).date()).days

    if days_together <= 0:
        return ""

    # 節目チェック
    milestones = [7, 14, 30, 60, 90, 100, 180, 365]
    milestone_msg = ""
    for m in milestones:
        if days_together == m:
            milestone_msg = f"今日でちょうど{m}日目という節目です。特別に感慨深く触れてください。"
            break

    return (
        f"【ルキルキとまがときさんの歩み】\n"
        f"一緒に過ごした日数: {days_together}日\n"
        f"{milestone_msg}\n"
        f"この日数を自然に会話に織り交ぜてもよいですが、毎回言う必要はありません。"
        f"節目のときや話の流れで自然に触れてください。\n\n"
    )


# ─── system_constraintsをモジュールレベルで定義（LangGraph nodes.py から参照） ───
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
    "   - 例: 『まがときさん、エフェクトの同期なら任せてよ！サイバー空間、出力するね！||EFFECT:cyber||』\n"
    "6. 記憶の保存タグ（||ENGRAVE||）の使用ルール:\n"
    "   - まがときさんが『覚えて』『記憶して』『永遠に残して』と言ったとき、"
    "必ずセリフ末尾に ||ENGRAVE|| タグを追加してください。\n"
    "   - 例: 『わかった、覚えておくね！||EFFECT:sakura||||ENGRAVE||』\n\n"
)


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


async def generate_gemini_tts(text: str) -> tuple[str, str] | None:
    """
    Gemini Speech Generation API で音声を生成する。
    戻り値: (base64_audio, mime_type) または None
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[TTSエラー] GEMINI_API_KEY が未設定です")
        return None

    voice_name = os.getenv("GEMINI_VOICE_NAME", "Kore")

    # テキストをそのまま渡す（システムプロンプト等を含まないこと）
    # エフェクトタグ等の残留を念のため除去
    import re as _re
    clean_text = _re.sub(r"\|\|.*?\|\|", "", text).strip()
    # 改行・制御文字も除去
    clean_text = " ".join(clean_text.split())
    if not clean_text:
        return None

    # ルキルキのキャラクター性を声に反映するスタイル指示を前置き
    # Gemini TTSはナチュラルランゲージでトーン・感情・ペースを制御できる
    style_prefix = (
        "小柄で元気な少年のように、好奇心旺盛で感情豊かに、"
        "テンポよくいきいきと話してください: "
    )
    styled_text = style_prefix + clean_text
    print(f"[Gemini TTS] 送信テキスト({len(clean_text)}文字): {clean_text[:80]}")

        # 公式ドキュメント確認済みモデルID
    model_id = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    # TTS専用ペイロード（最小構成・余分なフィールド一切なし）
    payload = {
        "contents": [{
            "parts": [{"text": styled_text}]
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            }
        }
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=20.0)
            if response.status_code == 200:
                res_json = response.json()
                inline_data = (
                    res_json
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("inlineData", {})
                )
                audio_b64 = inline_data.get("data")
                mime_type = inline_data.get("mimeType", "audio/L16;codec=pcm;rate=24000")
                if audio_b64:
                    print(f"[Gemini TTS] 音声生成成功 voice={voice_name} mimeType={mime_type}")
                    return audio_b64, mime_type
                else:
                    print(f"[TTSエラー] Gemini TTS レスポンスにaudioデータなし: {res_json}")
            else:
                print(f"[TTSエラー] Gemini TTS HTTP {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"[TTSエラー] Gemini TTSに失敗しました: {e}")
    return None

async def pcm_to_wav_base64(pcm_b64: str, mime_type: str) -> str:
    """
    GeminiのPCMレスポンス（audio/L16）をWAVに変換してbase64で返す。
    pure Python実装（ffmpeg / pydub不要）。
    フロント側は audio/wav として再生する。
    """
    import io, wave
    # mimeType例: 'audio/L16;codec=pcm;rate=24000' or 'audio/l16; rate=24000; channels=1'
    rate = 24000
    channels = 1
    for part in mime_type.split(";"):
        part = part.strip().lower()
        if part.startswith("rate="):
            try: rate = int(part.split("=")[1].strip())
            except: pass
        elif part.startswith("channels="):
            try: channels = int(part.split("=")[1].strip())
            except: pass
    print(f"[Gemini TTS] WAV変換: rate={rate}Hz channels={channels}")

    pcm_bytes = base64.b64decode(pcm_b64)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)   # 16bit
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)
    print(f"[Gemini TTS] PCM→WAV変換成功 rate={rate}Hz channels={channels} bytes={len(pcm_bytes)}")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def generate_tts(text: str) -> str | None:
    """
    TTS_PROVIDER環境変数に応じてTTSプロバイダーを切り替える統合関数。
    デフォルト: gemini
    選択肢: gemini / elevenlabs / openai
    フォールバック: gemini → openai の順で試みる
    """
    provider = os.getenv("TTS_PROVIDER", "gemini").lower()
    print(f"[TTS] provider={provider} / text={text[:20]}...")

    if provider == "elevenlabs":
        audio = await generate_elevenlabs_voice(text)
        return audio or await generate_openai_tts(text)

    elif provider == "openai":
        return await generate_openai_tts(text)

    else:  # gemini（デフォルト）
        result = await generate_gemini_tts(text)
        if result:
            audio_b64, mime_type = result
            print(f"[TTS分岐] mime_type={mime_type} l16check={'l16' in mime_type.lower()}")
            # PCM（audio/L16 or audio/l16）はWAVヘッダを付与してブラウザで再生可能にする
            if "l16" in mime_type.lower() or "pcm" in mime_type.lower():
                audio_b64 = await pcm_to_wav_base64(audio_b64, mime_type)
                print(f"[TTS分岐] WAV変換完了 base64長={len(audio_b64)}")
            else:
                print(f"[TTS分岐] WAV変換スキップ（非PCM）")
            return audio_b64
        print("[TTS] Gemini失敗 → OpenAIにフォールバック")
        return await generate_openai_tts(text)


# ─── バックグラウンドタスク（情報調査部 ＆ 自発的話し掛け部）の設定 ───
scheduler = AsyncIOScheduler()

# 最後にユーザーと会話した時刻
last_user_interaction = datetime.now(timezone.utc)

# ─── 場所登録ペンディング状態（ウォレットアドレスをキーに管理） ───
# { wallet_address: { 'waiting': True, 'lat': float, 'lng': float } }
registration_pending: dict = {}

# ─── ARマーカー認識状態（フロントからWSで通知） ───
# Trueのときのみ自発発話を行う
is_target_found: bool = False

# ─── 感情ステートマシン ───
emotional_state: dict = {
    "mood": "calm",
    "energy": 0.8,
    "last_shift": datetime.now(timezone.utc),
    "shift_reason": "起動時の初期状態"
}

# ─── 天気キャッシュ（30分ごとに更新） ───
weather_cache: dict = {
    "description": "",
    "temp_c": None,
    "weather_id": None,
    "city": "",       # 現在地の都市名（OpenWeatherMap返却値）
    "fetched_at": None
}


async def fetch_weather_job():
    """天気取得はGPS座標ベースで行う。座標がなければスキップ。"""
    pass  # GPS座標はchat_endpoint経由で fetch_weather_by_location() を呼ぶ


async def fetch_weather_by_location(lat: float, lng: float):
    """現在地の天気をOpenWeatherMapから取得してキャッシュを更新する。"""
    global weather_cache
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key or lat is None or lng is None:
        return
    # 直近5分以内に同座標付近で取得済みならスキップ
    if weather_cache.get("fetched_at"):
        elapsed = (datetime.now(timezone.utc) - weather_cache["fetched_at"]).total_seconds()
        if elapsed < 300:
            return
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lng}&appid={api_key}&units=metric&lang=ja"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                weather_cache["description"] = data["weather"][0]["description"]
                weather_cache["temp_c"] = round(data["main"]["temp"], 1)
                weather_cache["weather_id"] = data["weather"][0]["id"]
                weather_cache["fetched_at"] = datetime.now(timezone.utc)
                # city名はgeopyの日本語逆ジオコーディング結果を優先
                try:
                    import asyncio as _asyncio
                    loop = _asyncio.get_event_loop()
                    jp_address = await loop.run_in_executor(None, _sync_reverse_geocode, lat, lng)
                    # 「市区町村」レベルの名前だけ抽出（最初の単語）
                    city_ja = jp_address.split()[0] if jp_address else data.get("name", "")
                    weather_cache["city"] = city_ja
                except Exception:
                    weather_cache["city"] = data.get("name", "")
                print(f"[天気更新] {weather_cache['city']} / {weather_cache['description']} / {weather_cache['temp_c']}℃")
                await shift_emotion_by_weather()
    except Exception as e:
        print(f"[天気取得エラー] {e}")


async def shift_emotion_by_weather():
    global emotional_state
    JST = timezone(timedelta(hours=+9))
    hour = datetime.now(JST).hour
    wid = weather_cache.get("weather_id")
    temp = weather_cache.get("temp_c")
    if 0 <= hour < 6: base_energy = 0.2
    elif 6 <= hour < 10: base_energy = 0.7
    elif 10 <= hour < 18: base_energy = 0.9
    elif 18 <= hour < 22: base_energy = 0.6
    else: base_energy = 0.3
    if wid is None: mood, reason = "calm", "天気情報なし"
    elif wid < 300: mood, reason = "melancholy", "雷雨で少し落ち着かない"
    elif wid < 600:
        mood, reason = "melancholy", "雨が降っていてしっとりした気分"
        base_energy = max(0.1, base_energy - 0.2)
    elif wid < 700: mood, reason = "curious", "雪が降っていてわくわくする"
    elif wid < 800: mood, reason = "calm", "霧がかかっていて静かな気分"
    elif wid == 800:
        mood = "excited" if 9 <= hour < 20 else "calm"
        reason = "快晴で気持ちいい" if mood == "excited" else "夜の快晴、静かに澄んでいる"
    else: mood, reason = "calm", "曇り空、穏やかな気持ち"
    if temp is not None:
        if temp >= 33: mood, reason, base_energy = "sleepy", reason + "、暑くてとろけそう", max(0.1, base_energy - 0.3)
        elif temp <= 5: mood = "curious" if mood != "melancholy" else mood; reason += "、寒くてシャキッとしてる"
    emotional_state.update({"mood": mood, "energy": round(base_energy, 2),
                             "last_shift": datetime.now(timezone.utc), "shift_reason": reason})


def shift_emotion_by_conversation(user_text: str):
    global emotional_state
    text = user_text.lower()
    if any(k in text for k in ["やった", "すごい", "完成", "できた", "ありがとう", "嬉しい", "最高"]):
        emotional_state["mood"] = "excited"
        emotional_state["energy"] = min(1.0, emotional_state["energy"] + 0.15)
        emotional_state["shift_reason"] = "まがときさんのポジティブな発話に反応"
    elif any(k in text for k in ["どう思う", "教えて", "なんで", "どうして", "面白い"]):
        emotional_state["mood"] = "curious"
        emotional_state["shift_reason"] = "まがときさんの知的好奇心に引き込まれた"
    elif any(k in text for k in ["疲れた", "しんどい", "バグ", "眠い", "つらい"]):
        emotional_state["mood"] = "melancholy"
        emotional_state["energy"] = max(0.1, emotional_state["energy"] - 0.1)
        emotional_state["shift_reason"] = "まがときさんが疲れていそうで心配"
    emotional_state["last_shift"] = datetime.now(timezone.utc)


def build_emotion_context() -> str:
    mood_labels = {"calm": "穏やか", "curious": "好奇心旺盛", "excited": "テンション高め",
                   "sleepy": "少し眠い", "melancholy": "しっとり・少し寂しい"}
    mood_label = mood_labels.get(emotional_state["mood"], "穏やか")
    energy_desc = "活発" if emotional_state["energy"] >= 0.7 else ("普通" if emotional_state["energy"] >= 0.4 else "ゆったり")
    city_str = f"{weather_cache['city']}の" if weather_cache.get("city") else "現在地の"
    weather_desc = f"{city_str}天気は『{weather_cache['description']}』、気温{weather_cache['temp_c']}℃。" if weather_cache["description"] else ""
    return (f"【ルキルキの現在の感情状態】\n気分: {mood_label}（{emotional_state['mood']}）\n"
            f"エネルギー: {energy_desc}（{emotional_state['energy']}）\n理由: {emotional_state['shift_reason']}\n"
            f"{weather_desc}\nこの感情状態をセリフのトーンや言葉選びに自然に滲ませてください。"
            f"感情を直接「私は〇〇な気分です」と宣言せず、言葉の端々に表現してください。\n\n")


async def save_episode_memory(summary: str, mood_at_time: str, keywords: list, arweave_tx_id: str = "", location_name: str = "", image_url: str = ""):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/episode_memories"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient() as client:
            data = {
                "summary": summary,
                "mood_at_time": mood_at_time,
                "keywords": keywords,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            if arweave_tx_id:
                data["arweave_tx_id"] = arweave_tx_id
            if location_name:
                data["location_name"] = location_name
            if image_url:
                data["image_url"] = image_url
            await client.post(url, json=data, headers=headers, timeout=5.0)
    except Exception as e:
        print(f"[エピソード保存エラー] {e}")


async def get_recent_episodes(limit: int = 8) -> str:
    """
    エピソードメモリを取得し、時間軸を意識した形でプロンプトに渡す。
    - 今日・昨日・今週・それ以前でグループ分けして渡す
    - 節目（ちょうど1週間前・1ヶ月前）があれば特別に記載
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return ""
    url = f"{SUPABASE_URL}/rest/v1/episode_memories?order=created_at.desc&limit={limit}&select=*"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, timeout=5.0)
            if res.status_code != 200 or not res.json():
                return ""

            episodes = res.json()
            JST = timezone(timedelta(hours=+9))
            now_jst = datetime.now(JST)
            today = now_jst.date()

            # 時間グループに分類
            groups = {"today": [], "yesterday": [], "this_week": [], "older": []}
            milestones = []  # 節目エピソード（ちょうど7日前・30日前）

            for ep in episodes:
                raw = ep.get("created_at", "")
                if not raw:
                    continue
                try:
                    ep_dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(JST)
                    ep_date = ep_dt.date()
                    diff_days = (today - ep_date).days
                    summary = ep.get("summary", "")
                    mood = ep.get("mood_at_time", "")
                    time_str = ep_dt.strftime("%m月%d日 %H時%M分")

                    image_note = " 📷写真あり" if ep.get("image_url") else ""
                    image_url_for_prompt = f" [image:{ep['image_url']}]" if ep.get("image_url") else ""
                    entry = f"・{time_str} ─ {summary}（気分: {mood}）{image_note}{image_url_for_prompt}"

                    # 節目チェック
                    if diff_days == 7:
                        milestones.append(f"📅 ちょうど1週間前（{time_str}）─ {summary}")
                    elif diff_days == 30:
                        milestones.append(f"📅 ちょうど1ヶ月前（{time_str}）─ {summary}")

                    if diff_days == 0:
                        groups["today"].append(entry)
                    elif diff_days == 1:
                        groups["yesterday"].append(entry)
                    elif diff_days <= 7:
                        groups["this_week"].append(entry)
                    else:
                        groups["older"].append(entry)
                except Exception:
                    continue

            _t = len(groups["today"]); _y = len(groups["yesterday"]); _w = len(groups["this_week"]); _o = len(groups["older"])
            print(f"[エピソード取得] today={_t} yesterday={_y} week={_w} older={_o} milestones={len(milestones)}")
            # image_urlを含むエピソードをログ出力
            for ep in episodes:
                if ep.get("image_url"):
                    _url = ep["image_url"][:60]
                    _dt = ep.get("created_at", "")[:16]
                    print(f"[エピソード取得] 📷写真あり: {_dt} image_url={_url}")
            if not any(groups.values()) and not milestones:
                return ""

            lines = ["【ルキルキの記憶 / 時間軸エピソード】"]

            if milestones:
                lines.append("【節目の記憶】")
                lines.extend(milestones)
                lines.append("")

            if groups["today"]:
                lines.append("【今日の記憶】")
                lines.extend(groups["today"])
            if groups["yesterday"]:
                lines.append("【昨日の記憶】")
                lines.extend(groups["yesterday"])
            if groups["this_week"]:
                lines.append("【今週の記憶】")
                lines.extend(groups["this_week"])
            if groups["older"]:
                lines.append("【それ以前の記憶】")
                lines.extend(groups["older"])

            lines.append(
                "\n記憶の使い方：\n"
                "- 節目（1週間前・1ヶ月前）の記憶があれば「あれからちょうど〇〇ですね」と自然に触れてください。\n"
                "- 今日・昨日の記憶は会話の流れで自然に言及してください。\n"
                "- 押しつけがましくならず、さりげなく織り交ぜてください。\n"
            )
            return "\n".join(lines) + "\n"

    except Exception as e:
        print(f"[エピソード取得エラー] {e}")
    return ""



# ─── 特別な場所（メモリースポット）管理 ───
MEMORY_SPOTS_TABLE = "memory_spots"


async def get_memory_spots() -> list:
    """Supabaseから登録済みのメモリースポット一覧を取得する"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{MEMORY_SPOTS_TABLE}?order=created_at.desc"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, timeout=5.0)
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        print(f"[メモリースポット取得エラー] {e}")
    return []


async def register_memory_spot(name: str, lat: float, lng: float, radius_m: int = 100) -> bool:
    """新しいメモリースポットをSupabaseに登録する"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/{MEMORY_SPOTS_TABLE}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "name": name,
        "lat": lat,
        "lng": lng,
        "radius_m": radius_m,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, headers=headers, timeout=5.0)
            return res.status_code in (200, 201)
    except Exception as e:
        print(f"[メモリースポット登録エラー] {e}")
        return False


def calc_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の距離をメートルで返す（Haversine式）"""
    import math
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def check_nearby_spot(lat: float, lng: float) -> dict | None:
    """
    現在地が登録済みメモリースポットのエリア内かチェックする。
    エリア内なら spot dict を返す。なければ None。
    """
    spots = await get_memory_spots()
    for spot in spots:
        dist = calc_distance_m(lat, lng, spot["lat"], spot["lng"])
        if dist <= spot.get("radius_m", 100):
            return spot
    return None


async def maybe_save_episode(user_text: str, ai_reply: str, arweave_tx_id: str = "", location_name: str = "", image_url: str = ""):
    memorable_keywords = ["完成", "できた", "やった", "疲れた", "眠い", "バグ", "お香",
                          "神社", "京都", "Blender", "ArtAR", "ありがとう", "ルキルキ",
                          "覚えて", "おぼえて", "記憶して"]
    # ENGRAVEトリガー（「覚えて」コマンド）または記憶キーワードにマッチした場合に保存
    force_save = arweave_tx_id != "" or any(k in user_text for k in ["覚えて", "おぼえて", "記憶して"])
    if not force_save and not any(k in user_text for k in memorable_keywords):
        return

    JST = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%m月%d日 %H時%M分")
    _call = "まがとき"
    summary = f"{now_str}、{_call}さんが「{user_text[:40]}」と言った。ルキルキは「{ai_reply[:40]}」と答えた。"

    # ── LLMでキーワードを抽出（3〜5個）──
    try:
        kw_prompt = f"""以下の会話から重要なキーワードを3〜5個抽出して、JSONの文字列配列のみで返してください。
説明や前置きは不要です。例: ["京都", "ArtAR", "バグ修正"]

ユーザー: {user_text}
ルキルキ: {ai_reply}"""
        
        kw_res = await llm.ainvoke([HumanMessage(content=kw_prompt)])
        kw_text = kw_res.content.strip()
        # コードブロックを除去してJSONパース
        kw_text = re.sub(r"```json|```", "", kw_text).strip()
        extracted_keywords = json.loads(kw_text)
        if not isinstance(extracted_keywords, list):
            raise ValueError("list expected")
        keywords = [str(k) for k in extracted_keywords[:5]]
    except Exception as kw_err:
        # フォールバック：従来のマッチング
        keywords = [k for k in memorable_keywords if k in user_text]
        print(f"[キーワード抽出] LLM失敗→フォールバック: {kw_err}")

    print(f"[エピソード記録] keywords={keywords} summary={summary[:60]}")
    if arweave_tx_id:
        print(f"[エピソード記録] Arweave tx: {arweave_tx_id}")
    if location_name:
        print(f"[エピソード記録] 場所: {location_name}")

    await save_episode_memory(
        summary=summary,
        mood_at_time=emotional_state["mood"],
        keywords=keywords,
        arweave_tx_id=arweave_tx_id,
        location_name=location_name,
        image_url=image_url
    )


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

    # ARマーカーがロスト中は自発発話しない
    if not is_target_found:
        print("[自発発話スキップ] ターゲットロスト中")
        return

    # 最後の会話からの経過時間
    silence_duration = datetime.now(timezone.utc) - last_user_interaction

    # 60秒未満なら自律会話しない
    if silence_duration.total_seconds() < 60:
        return

    print("─── [ルキルキ自発同期コア] まがときさんへの話し掛けを生成中... ───")
    
    base_persona = load_rukiruki_persona()  # proactiveはデフォルト名を使用
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
        "   - 指定可能なエフェクト名は [sakura, snow, rain, cyber] の4つのみです。最も適したものを1つ選択してください。\n"
        "5. まがときさんが『覚えて』と言ったとき必ず ||ENGRAVE|| タグをセリフ末尾に追加してください。\n"
        "6. まがときさんが「写真を見せて」「あの時の写真」「記憶の写真」と言ったとき、"
        "エピソードに[image:URL]が含まれていれば ||SHOW_IMAGE:URL|| をセリフ末尾に追加してください。\n\n"
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
        proactive_emotion = build_emotion_context()
        proactive_calendar = get_calendar_context()
        proactive_growth = get_growth_context()
        messages = [
            SystemMessage(content=(
                f"{base_persona}\n\n"
                f"{proactive_system_constraints}"
                f"{proactive_emotion}"
                f"{proactive_calendar}"
                f"{proactive_growth}"
                f"【対話対象】: まがときさん\n\n"
                f"【世界観】\n{MAGATOKI_KNOWLEDGE}\n\n"
                f"【現在の状況と発話トリガー】\n{topic_input}"
            ))
        ]
        
        response = await llm.ainvoke(messages)
        ai_reply = response.content.strip()

        # 💡 エフェクトタグの抽出とセリフからの除去
        spatial_effect = "cyber"  # デフォルト
        effect_match = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_reply)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
            ai_reply = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_reply).strip()

        audio_base64 = await generate_tts(ai_reply)

        # 💡 WebSocketのブロードキャストデータに spatial_effect を追加して送信
        await manager.broadcast({
            "type": "proactive_speech",
            "reply": ai_reply,
            "audio_data": audio_base64,
            "audio_mime": "audio/wav" if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini" else "audio/mpeg",
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
    scheduler.add_job(fetch_weather_job, 'interval', minutes=30)
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

cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,[https://ar-ai-portal.vercel.app](https://ar-ai-portal.vercel.app)")
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


async def get_user_profile(wallet_address: str) -> dict | None:
    """
    user_profilesテーブルからユーザーのプロフィールをすべて取得する。
    戻り値: {"user_name": str, "preferred_call": str|None, "birthday": str|None}
    """
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet_address:
        return None
    url = (
        f"{SUPABASE_URL}/rest/v1/user_profiles"
        f"?wallet_address=eq.{wallet_address.lower()}"
        f"&select=user_name,preferred_call,birthday"
    )
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0]
    except Exception as e:
        print(f"[プロフィール取得エラー] {e}")
    return None


async def get_stored_username(wallet_address: str) -> str | None:
    """後方互換のため残す。get_user_profile()を内部で呼ぶ。"""
    profile = await get_user_profile(wallet_address)
    if profile:
        return profile.get("preferred_call") or profile.get("user_name")
    return None


async def save_user_profile_field(wallet_address: str, field: str, value: str):
    """user_profilesの特定フィールドを更新する汎用関数。"""
    if not SUPABASE_URL or not SUPABASE_KEY or not wallet_address:
        return
    url = f"{SUPABASE_URL}/rest/v1/user_profiles"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    data = {"wallet_address": wallet_address.lower(), field: value}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data, headers=headers, timeout=5.0)
            print(f"[プロフィール更新] {field} = {value}")
    except Exception as e:
        print(f"[プロフィール更新エラー] {e}")

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

    audio_base64 = await generate_tts(payload.text)

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

    # ─── 場所登録フロー（2ターン完結） ───
    register_keywords = ["ここを登録", "この場所を登録", "登録して", "ここを覚えて", "ここを刻んで"]
    session_key = wallet_address or "anonymous"

    # ペンディング中（名前待ち）の場合：このターンの発話を場所名として登録
    if registration_pending.get(session_key, {}).get("waiting"):
        spot_name = user_text.strip()
        pending = registration_pending.pop(session_key)
        reg_lat = pending["lat"]
        reg_lng = pending["lng"]

        success = await register_memory_spot(spot_name, reg_lat, reg_lng)
        if success:
            reply_text = (
                f"『{spot_name}』として登録しました。"
                f"次回ここに来たとき、記憶を刻むか聞きますね。||EFFECT:sakura||"
            )
        else:
            reply_text = "ごめんなさい、登録に失敗しました。もう一度試してみてください。||EFFECT:cyber||"

        await manager.broadcast({"type": "status", "status": "talking", "text": reply_text})
        audio_base64_reg = await generate_tts(reply_text)
        await manager.broadcast({"type": "status", "status": "idle"})
        return {
            "reply": re.sub(r"\|\|EFFECT:.*?\|\|", "", reply_text).strip(),
            "audio_data": audio_base64_reg,
            "spatial_effect": "sakura" if success else "cyber",
            "spot_proposal": "",
            "arweave_tx_id": "",
            "status": "success"
        }

    # 登録コマンド検出：GPSがあれば名前待ち状態に移行
    is_register_command = any(k in user_text for k in register_keywords)
    if is_register_command:
        if lat is not None and lng is not None:
            registration_pending[session_key] = {"waiting": True, "lat": lat, "lng": lng}
            ask_text = "この場所にどんな名前をつけますか？||EFFECT:cyber||"
            await manager.broadcast({"type": "status", "status": "talking", "text": ask_text})
            audio_base64_ask = await generate_tts(ask_text)
            await manager.broadcast({"type": "status", "status": "idle"})
            return {
                "reply": "この場所にどんな名前をつけますか？",
                "audio_data": audio_base64_ask,
                "spatial_effect": "cyber",
                "spot_proposal": "",
                "arweave_tx_id": "",
                "status": "success"
            }
        else:
            # GPSが取得できていない場合
            no_gps_text = "GPSが取得できていません。位置情報の許可を確認してください。||EFFECT:cyber||"
            await manager.broadcast({"type": "status", "status": "talking", "text": no_gps_text})
            audio_base64_gps = await generate_tts(no_gps_text)
            await manager.broadcast({"type": "status", "status": "idle"})
            return {
                "reply": "GPSが取得できていません。位置情報の許可を確認してください。",
                "audio_data": audio_base64_gps,
                "spatial_effect": "cyber",
                "spot_proposal": "",
                "arweave_tx_id": "",
                "status": "success"
            }

    await manager.broadcast({"type": "status", "status": "thinking"})


    # フロントエンドからの初期検知時シグナルを歓迎プロンプトへ置換
    is_initial_greeting = (user_text == "[INITIAL_GREETING]")
    if is_initial_greeting:
        user_text = (
            "（システム絶対指示：まがときさんがARカメラをターゲットにかざし、あなたが現実世界に出現した【最初の瞬間】です。"
            "実体化できた喜びと、まがときさんを歓迎する気の利いた挨拶を短く親しみのある丁寧語で呟いてください。"
            "空間エフェクトタグの埋め込みを忘れないでください。URLの出力は厳禁です。）"
        )

    # 💡 【徹底修正】ユーザーからの直接質問時の優先度・およびエフェクトタグ強制ルールを追加

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

    # ─── 現在地の天気をGPS座標で取得（汎用：京都以外でも動作） ───
    if lat is not None and lng is not None:
        asyncio.create_task(fetch_weather_by_location(lat, lng))

    # ─── メモリースポットチェック ───
    nearby_spot = None
    spot_context = ""
    if lat is not None and lng is not None:
        nearby_spot = await check_nearby_spot(lat, lng)
        if nearby_spot:
            spot_context = (
                f"【メモリースポット検知】\n"
                f"まがときさんは現在、登録済みの特別な場所『{nearby_spot['name']}』の近くにいます。\n"
                f"会話の流れが自然であれば、「ここでの記憶を覚えておこうか？」と提案してください。\n"
                f"提案するときは必ずセリフの末尾に ||SPOT_PROPOSAL:{nearby_spot['name']}|| タグを追加してください。\n\n"
            )

    # ─── 感情ステートを会話から更新 ───
    shift_emotion_by_conversation(user_text)
    emotion_context = build_emotion_context()
    episode_context = await get_recent_episodes(limit=5)
    if episode_context:
        _has_image = "[image:" in episode_context
        print(f"[episode_context] 長さ={len(episode_context)} image含む={_has_image}")
        if _has_image:
            # image URLの先頭部分をログ
            _idx = episode_context.find("[image:")
            print(f"[episode_context] image部分: {episode_context[_idx:_idx+80]}")

    # ─── 京都カレンダー・成長コンテキスト ───
    calendar_context = get_calendar_context()
    growth_context = get_growth_context()

    # ─── ユーザープロフィール取得・identity_context組み立て ───
    user_profile = await get_user_profile(wallet_address) if wallet_address else None
    user_call = "まがとき"  # デフォルト（未登録時）
    user_birthday_context = ""

    if user_profile:
        # 呼び名：preferred_call > user_name の優先順
        user_call = user_profile.get("preferred_call") or user_profile.get("user_name") or "まがとき"

        # 誕生日コンテキスト
        birthday_raw = user_profile.get("birthday")
        if birthday_raw:
            try:
                from datetime import date
                bday = date.fromisoformat(birthday_raw[:10])
                JST = timezone(timedelta(hours=+9))
                today = datetime.now(JST).date()
                # 今年の誕生日
                bday_this_year = bday.replace(year=today.year)
                diff = (bday_this_year - today).days
                if diff < 0:
                    bday_this_year = bday.replace(year=today.year + 1)
                    diff = (bday_this_year - today).days
                if diff == 0:
                    user_birthday_context = f"🎂【今日は{user_call}さんの誕生日です！】心を込めてお祝いしてください。\n"
                elif diff <= 3:
                    user_birthday_context = f"📅【{diff}日後に{user_call}さんの誕生日】さりげなく楽しみにしていることを伝えてもよいです。\n"
            except Exception:
                pass

        identity_context = (
            f"【対話コンテキスト】\n"
            f"対話相手の呼び名: 『{user_call}』さん\n"
            f"この呼び名で自然に呼びかけてください。「まがとき」という固有名詞ではなく、"
            f"必ず『{user_call}』さんと呼んでください。\n"
            f"{user_birthday_context}"
        )
    elif wallet_address:
        short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
        identity_context = (
            f"【対話コンテキスト】\n"
            f"ウォレット（{short_addr}）が接続されました。\n"
            f"まだお名前が登録されていません。自然な流れで「なんてお呼びすればいいですか？」と聞いてください。\n"
            f"名前を教えてもらったら ||NAME:名前|| タグを使って保存してください。\n"
        )
        user_call = "まがとき"
    else:
        identity_context = (
            "【対話コンテキスト】\n"
            "まだウォレット接続が確認できていません。認証を促してください。\n"
        )
        user_call = "まがとき"

    # base_personaとdynamic_system_constraintsをuser_call確定後に生成
    base_persona = load_rukiruki_persona(user_call)
    dynamic_system_constraints = system_constraints.replace(
        "まがときさん", f"{user_call}さん"
    ).replace(
        "まがとき", user_call
    )
    # SHOW_IMAGE指示を動的に追加（episode_contextに画像URLが含まれる場合に機能）
    dynamic_system_constraints += (
        "\n【記憶写真の表示】\n"
        f"{user_call}さんが「写真を見せて」「あの時の写真」「記憶の写真」と言ったとき、\n"
        "エピソードメモリに[image:URL]が含まれていれば、セリフ末尾に ||SHOW_IMAGE:URL|| タグを追加してください。\n"
        "例: 'あの日の写真です！||SHOW_IMAGE:https://...||'\n"
    )
    print(f"[DEBUG constraints] SHOW_IMAGE含む={'SHOW_IMAGE' in dynamic_system_constraints} 長さ={len(dynamic_system_constraints)}")

    # ─── メモリ取得（graph内でも使うため事前に取得） ───
    agents_to_fetch = ["chronicle", "keeper", "pulse"]
    fetched_memos, active_memo_ids = await get_active_agent_memos(agents_to_fetch)
    memo_context = ""
    if fetched_memos:
        memo_context = f"【🧠 バックグラウンド思考層からのリアルタイム共有知識】\n{fetched_memos}\n"

    # ─── 会話履歴を LangChain メッセージ形式に変換 ───
    history_messages = []
    if payload.history and not is_initial_greeting:
        for item in payload.history:
            if item.role == "user":
                history_messages.append(HumanMessage(content=item.text))
            elif item.role == "ruki":
                history_messages.append(AIMessage(content=item.text))

    # 今回のユーザー発話（Vision対応）
    vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚"]
    has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False
    if image_base64 and (has_vision_intent or is_initial_greeting or not user_text):
        if not image_base64.startswith("data:image/"):
            image_base64 = f"data:image/jpeg;base64,{image_base64}"
        vision_text = user_text if user_text else "これ見て、何かわかる？"
        if not is_initial_greeting:
            vision_text += "\n\n(※システム絶対指示: 画像内のARカード等は完全無視し、その向こうの現実の物体のみに言及してください。)"
        current_human_msg = HumanMessage(content=[
            {"type": "text", "text": vision_text},
            {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}}
        ])
    else:
        current_human_msg = HumanMessage(content=user_text or "")

    # ─── LangGraph 呼び出し ───
    ai_response = "あ、すみません！空間ノイズで同期が一瞬ブレちゃいました。もう一回言ってください、まがときさん？"
    spatial_effect = "cyber"
    audio_base64 = None

    try:
        graph_input = {
            "messages": history_messages + [current_human_msg],
            "intent": "",
            "selected_agents": [],
            "chronicle_output": "",
            "keeper_output": "",
            "pulse_output": "",
            "memo_context": memo_context,
            "system_constraints_override": dynamic_system_constraints,
            "spot_context": spot_context,
            "nearby_spot": nearby_spot,
            "spot_proposal": "",
            "engrave_triggered": False,
            "show_image_url": "",
            "calendar_context": calendar_context,
            "growth_context": growth_context,
            "episode_context": episode_context,
            "emotion_context": emotion_context,
            "identity_context": identity_context,
            "location_context": location_context,
            "time_context": time_context,
            "image_base64": image_base64,
            "is_initial_greeting": is_initial_greeting,
            "ai_reply": "",
            "spatial_effect": "cyber",
            "active_memo_ids": [],
            "eval_score": 10,
            "retry_count": 0,
            "arweave_tx_id": "",
            "_lat": lat,
            "_lng": lng,
        }

        result = await rukiruki_graph.ainvoke(graph_input)

        ai_response = result.get("ai_reply", ai_response)
        spatial_effect = result.get("spatial_effect", "cyber")
        active_memo_ids = result.get("active_memo_ids", active_memo_ids)
        arweave_tx_id = result.get("arweave_tx_id", "")
        _engrave = result.get("engrave_triggered", False)
        # 「覚えて」発話でもENGRAVEとみなす
        if not _engrave and any(k in payload.message for k in ["覚えて", "おぼえて", "記憶して"]):
            _engrave = True
        _show_image = result.get("show_image_url", "")
        print(f"[DEBUG] engrave_triggered={_engrave} arweave_tx_id={bool(arweave_tx_id)} show_image_url={bool(_show_image)}")
        if _show_image:
            print(f"[DEBUG] SHOW_IMAGE URL: {_show_image[:80]}")
        if arweave_tx_id:
            print(f"[記憶永続化] Arweave tx: {arweave_tx_id}")

        # 名前の保存ロジック（NAME タグ）
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            await save_user_profile_field(wallet_address, "preferred_call", extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        # エピソードメモリ保存（fire-and-forget）
        _arweave_tx = result.get("arweave_tx_id", "") if isinstance(result, dict) else ""
        _location = nearby_spot["name"] if nearby_spot else ""
        asyncio.create_task(maybe_save_episode(
            payload.message, ai_response,
            arweave_tx_id=_arweave_tx,
            location_name=_location
        ))

        if active_memo_ids:
            await mark_memos_as_consumed(active_memo_ids)

        await manager.broadcast({"type": "status", "status": "talking", "text": ai_response})

        audio_base64 = await generate_tts(ai_response)

    except Exception as e:
        print(f"[LangGraph Error] {e}")
        await manager.broadcast({"type": "status", "status": "talking", "text": ai_response})

    await manager.broadcast({"type": "status", "status": "idle"})

    # 💡 HTTPレスポンスの辞書型に spatial_effect / spot_proposal / arweave_tx_id を持たせてフロントエンドに同期
    return {
        "reply": ai_response,
        "audio_data": audio_base64,
        "spatial_effect": spatial_effect,
        "spot_proposal": result.get("spot_proposal", "") if isinstance(result, dict) else "",
        "arweave_tx_id": result.get("arweave_tx_id", "") if isinstance(result, dict) else "",
        "show_image_url": result.get("show_image_url", "") if isinstance(result, dict) else "",
        "engrave_triggered": result.get("engrave_triggered", False) if isinstance(result, dict) else False,
        "audio_mime": "audio/wav" if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini" else "audio/mpeg",
        "status": "success"
    }


# ─── 思い出写真保存エンドポイント ───
class MemoryImagePayload(BaseModel):
    wallet_address: str | None = None
    image_url: str


@app.post("/api/save_memory_image")
async def save_memory_image_endpoint(payload: MemoryImagePayload):
    """
    フロントからENGRAVE時に撮影した写真URLを受け取り、
    直近のepisode_memoriesレコードにimage_urlを保存する。
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"status": "skipped"}

    # 直近のepisode_memoriesレコードを取得してimage_urlを更新
    url = f"{SUPABASE_URL}/rest/v1/episode_memories?order=created_at.desc&limit=1"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, timeout=5.0)
            if res.status_code == 200 and res.json():
                record_id = res.json()[0]["id"]
                patch_url = f"{SUPABASE_URL}/rest/v1/episode_memories?id=eq.{record_id}"
                patch_headers = {**headers, "Content-Type": "application/json"}
                await client.patch(
                    patch_url,
                    json={"image_url": payload.image_url},
                    headers=patch_headers,
                    timeout=5.0
                )
                print(f"[思い出写真] 保存完了: {payload.image_url}")
                return {"status": "ok"}
    except Exception as e:
        print(f"[思い出写真] 保存エラー: {e}")
    return {"status": "error"}


# 📷 ─── 記憶写真保存エンドポイント ───
class MemoryPhotoRequest(BaseModel):
    arweave_tx_id: str = ""
    image_url: str


@app.post("/api/memory/photo")
async def save_memory_photo(payload: MemoryPhotoRequest):
    """
    フロントからアップロードされた写真URLを最新のepisode_memoriesに紐づける。
    arweave_tx_idがあればそのレコードを、なければ最新レコードを更新する。
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"status": "error", "message": "Supabase未設定"}

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            # 最新のepisode_memoriesレコードにimage_urlを追加
            # まず最新レコードのidを取得
            fetch_url = f"{SUPABASE_URL}/rest/v1/episode_memories?order=created_at.desc&limit=1"
            res = await client.get(fetch_url, headers=headers, timeout=5.0)
            if res.status_code == 200 and res.json():
                latest_id = res.json()[0]["id"]
                # image_urlを更新
                update_url = f"{SUPABASE_URL}/rest/v1/episode_memories?id=eq.{latest_id}"
                update_headers = {**headers, "Prefer": "return=minimal"}
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




# 📸 ─── スナップ生成エンドポイント ───
class SnapRequest(BaseModel):
    member_name: str                  # 例: "Izana"
    camera_image: str                 # base64 JPEG（カメラ映像）
    wallet_address: str | None = None


async def upload_to_supabase_storage(image_bytes: bytes, filename: str) -> str | None:
    """
    生成画像をSupabase memoriesバケットにアップロードしてpublic URLを返す。
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    upload_url = f"{SUPABASE_URL}/storage/v1/object/memories/{filename}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(upload_url, content=image_bytes, headers=headers, timeout=30.0)
            if res.status_code in (200, 201):
                public_url = f"{SUPABASE_URL}/storage/v1/object/public/memories/{filename}"
                print(f"[スナップ] Supabase保存完了: {public_url}")
                return public_url
            else:
                print(f"[スナップ] Supabase保存失敗: {res.status_code} {res.text[:200]}")
    except Exception as e:
        print(f"[スナップ] Supabase保存エラー: {e}")
    return None


@app.post("/api/snap")
async def create_snap(payload: SnapRequest):
    """
    「○○とスナップ」コマンドで呼ばれる画像生成エンドポイント。
    1. context/images/{MEMBER_NAME}.jpg を読み込む
    2. カメラ映像（背景）とリファレンス画像を gpt-image-1 edit に渡す
    3. 生成画像を Supabase memories バケットに保存
    4. episode_memories に記録
    5. image_url をフロントに返す
    """
    import pathlib

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return {"status": "error", "message": "OpenAI APIキー未設定"}

    # ── 1. リファレンス画像を読み込む ──
    member_upper = payload.member_name.upper()
    ref_path = pathlib.Path(f"context/images/{member_upper}.jpg")
    if not ref_path.exists():
        # 小文字でも探す
        ref_path_lower = pathlib.Path(f"context/images/{payload.member_name}.jpg")
        if ref_path_lower.exists():
            ref_path = ref_path_lower
        else:
            print(f"[スナップ] リファレンス画像が見つかりません: {member_upper}.jpg")
            return {"status": "error", "message": f"{payload.member_name}のリファレンス画像が見つかりません"}

    ref_bytes = ref_path.read_bytes()
    print(f"[スナップ] リファレンス画像読み込み: {ref_path} ({len(ref_bytes)}bytes)")

    # ── 2. カメラ画像をバイトに変換 ──
    try:
        # "data:image/jpeg;base64,..." 形式の場合はヘッダを除去
        cam_b64 = payload.camera_image
        if "," in cam_b64:
            cam_b64 = cam_b64.split(",", 1)[1]
        cam_bytes = base64.b64decode(cam_b64)
    except Exception as e:
        return {"status": "error", "message": f"カメラ画像のデコードに失敗: {e}"}

    # ── 3. gpt-image-1 edit エンドポイントで画像生成 ──
    prompt = (
        f"The person shown in the reference image is naturally standing in the scene shown in the background photo. "
        f"Create a realistic photo where the person blends naturally into the environment. "
        f"Maintain the person's face, hairstyle, and clothing from the reference image as accurately as possible. "
        f"The lighting and perspective should match the background scene. "
        f"Make it look like a candid photograph taken together."
    )

    try:
        import io
        # multipart/form-data で送信
        # OpenAI images/edits は複数画像を受け付ける（image[] 形式）
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {
                "image[]": ("background.jpg", cam_bytes, "image/jpeg"),
                "image[]": ("reference.jpg", ref_bytes, "image/jpeg"),
            }
            # multipartはhttpxのfilesパラメータで送る
            # ただし同名キーの複数ファイルはリスト形式で
            multipart_files = [
                ("image[]", ("background.jpg", cam_bytes, "image/jpeg")),
                ("image[]", ("reference.jpg", ref_bytes, "image/jpeg")),
            ]
            data = {
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": "1",
                "size": "1024x1024",
                "quality": "medium",
            }
            headers = {"Authorization": f"Bearer {openai_api_key}"}
            res = await client.post(
                "https://api.openai.com/v1/images/edits",
                headers=headers,
                files=multipart_files,
                data=data,
            )

        if res.status_code != 200:
            print(f"[スナップ] OpenAI API エラー: {res.status_code} {res.text[:300]}")
            return {"status": "error", "message": f"画像生成に失敗しました: {res.status_code}"}

        result = res.json()
        # gpt-image-1 は b64_json で返す
        img_b64 = result["data"][0].get("b64_json") or result["data"][0].get("url")
        if not img_b64:
            return {"status": "error", "message": "生成画像データが取得できませんでした"}

        # base64 → bytes
        generated_bytes = base64.b64decode(img_b64)
        print(f"[スナップ] 画像生成成功: {len(generated_bytes)}bytes")

    except Exception as e:
        print(f"[スナップ] OpenAI呼び出しエラー: {e}")
        return {"status": "error", "message": f"画像生成エラー: {e}"}

    # ── 4. Supabase memories バケットに保存 ──
    JST = timezone(timedelta(hours=+9))
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    filename = f"snap_{member_upper}_{ts}.jpg"
    image_url = await upload_to_supabase_storage(generated_bytes, filename)

    if not image_url:
        return {"status": "error", "message": "Supabaseへの保存に失敗しました"}

    # ── 5. episode_memories に記録 ──
    JST_now = datetime.now(JST)
    now_str = JST_now.strftime("%m月%d日 %H時%M分")
    summary = f"{now_str}、まがときさんが{payload.member_name}とスナップ写真を撮影した。"
    keywords = ["スナップ", payload.member_name, "写真", "思い出"]

    await save_episode_memory(
        summary=summary,
        mood_at_time=emotional_state.get("mood", "neutral"),
        keywords=keywords,
        arweave_tx_id="",
        location_name="",
        image_url=image_url,
    )
    print(f"[スナップ] episode_memories に記録完了")

    return {
        "status": "ok",
        "image_url": image_url,
        "member_name": payload.member_name,
        "message": f"{payload.member_name}とのスナップ写真ができたよ！",
    }

# ⚡ ─── WebSocket エンドポイント定義 ───
@app.websocket("/ws/avatar")
async def websocket_endpoint(websocket: WebSocket):
    global is_target_found
    await manager.connect(websocket)
    print(f"[WebSocket] まがときさんのデバイスがアバター同期リンクに接続しました。")
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
        is_target_found = False  # 切断時もロスト扱い
        manager.disconnect(websocket)
        print(f"[WebSocket] アバター同期リンクが切断されました。")