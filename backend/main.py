# main.py
# ─────────────────────────────────────────────────────────────────────────────
# FastAPI アプリのエントリーポイント。
# ここには「ルーティング定義」と「起動 / 停止制御」のみを記述する。
# ビジネスロジックはすべて services/* に委譲する。
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import json
import asyncio
import base64
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()                                    # ← ここに移動（importの直後、他のservices importより前）


from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage
from services.resilient_llm import build_fast_llm

# ─── Gemini無料枠フォールバック時のひとこと ───
# used_fallback フラグが立った回にランダムで1つ選び、ai_responseの末尾に添える。
FALLBACK_QUIPS = [
    "（あ、今のは無料枠オーバーでOpenAIさんに助けてもらったよ！）",
    "（えへへ、Geminiの無料枠を使い切っちゃったみたい。ピンチヒッターありがとう！）",
    "（今のひとこと、実はOpenAIさんにバトンタッチしてもらいました〜！）",
]
from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── services インポート ───
import services.state as state
from services.tts import generate_tts
from services.emotion import (
    fetch_weather_by_location,
    fetch_weather_job,
    shift_emotion_by_conversation,
    build_emotion_context,
    get_calendar_context,
    get_growth_context,
)
from services.location import locate_current_position, judge_magatoki_sector
from services.memory import (
    save_episode_memory,
    get_recent_episodes,
    maybe_save_episode,
    update_episode_image_url,
    find_episode_image_by_location,
    find_episode_image_by_proximity,
    check_nearby_spot,
    register_memory_spot,
    get_active_agent_memos,
    mark_memos_as_consumed,
    get_user_profile,
    save_username_to_db,
    save_user_profile_field,
    should_generate_ai_news_today,
    detect_meal_mention,
    save_meal_log,
    get_recent_meal_logs,
    build_meal_context,
    should_check_meal_reminder,
)

from services.glasses.hud_status import glasses_hud_manager

from services.snap import generate_snap, upload_to_supabase_storage
from services.books import fetch_book_by_isbn, save_reading_log
from services.character_bible import generate_mind_profile, generate_growth_note
from services import timeline
from services import prompt_builder
from services.scheduler import (
    auto_research_job,
    proactive_talk_job,
    trigger_proactive_speech,
    calendar_prep_job,
    daily_ai_news_job,
    meal_reminder_job,
    reminder_prep_job,
    weather_prep_job,
    spot_proximity_job,
    calendar_upcoming_job,
)
from services.reminders import get_upcoming_reminders
from services.health import save_daily_steps, get_recent_steps, build_step_context, get_step_history
from services.journey import apply_daily_steps, announce_milestone, get_journey_status, get_journey_progress, current_position, journey_day_count, get_journey_full_status
from services.weather_radar import fetch_radar_tile
from services.profile import build_memory_base
from services.user_growth import get_recent_growth_notes
from services.persona import (
    load_rukiruki_persona,
    load_magatoki_context,
    build_dynamic_constraints,
)

# agents モジュール（既存のまま）
from agents.router import analyze_and_route
from agents.graph import build_rukiruki_graph

# ─── LLM / ツール ───
# モデルは .env の LLM_MODEL_FAST で一括管理（nodes.py / router.py と同じ環境変数）。
# 2026-07-05: Router/Agent/Evaluator/Vision系はコスト最適化とプロバイダー分散のため
# OpenAIからGeminiへ移行（Synthesizer=llm_synthはOpenAIのまま維持）。
# 2026-07-05追記: Gemini無料枠のレート制限/クォータ枯渇時にOpenAI(gpt-4o-mini)へ
# 自動フォールバックするResilientLLMに変更（弱点解消）。
llm = build_fast_llm(temperature=0.8, name="MAIN-LLM")
search_tool = TavilySearch(max_results=2)  # type: ignore
llm_with_tools = llm.bind_tools([search_tool, locate_current_position])

# 起動時に一度だけ読み込む知識ベース
MAGATOKI_KNOWLEDGE = load_magatoki_context()

# APScheduler
scheduler = AsyncIOScheduler()

# LangGraph グラフ（lifespan で初期化）
rukiruki_graph = None


# ─────────────────────────────────────────────────────────────────────────────
# ライフサイクル管理
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global rukiruki_graph
    rukiruki_graph = build_rukiruki_graph()

    # Even G2の歩数バッジ用キャッシュを起動時に一度だけDBから初期化する。
    # これが無いと、Render再起動直後は次にhealth_updateが叩かれる（＝翌日0時）
    # までバッジが空のままになってしまう。
    try:
        recent = await get_recent_steps(days=1)
        if recent:
            state.latest_step_count = recent[0].get("steps")
    except Exception as e:
        print(f"[起動時] 歩数キャッシュの初期化に失敗しました: {e}")

    # 東海道の旅：現在地キャッシュも同様に起動時にDBから初期化する
    # （latest_step_countと同じ理由。これを忘れていたため、Render再起動後は
    # 次のhealth_updateまでバッジから旅の位置情報が消えてしまっていた）。
    try:
        progress = await get_journey_progress()
        pos = current_position(progress["total_distance_km"])
        day_count = journey_day_count(progress["started_at"]) if progress["started_at"] else 1
        state.latest_journey_summary = f"{pos['last_station']['name']} {progress['total_distance_km']:.1f}km（第{day_count}日目）"
        state.latest_journey_ratio = pos["progress_ratio"]
    except Exception as e:
        print(f"[起動時] 旅の現在地キャッシュの初期化に失敗しました: {e}")

    # FastAPI が動いているイベントループを保持する。
    # APScheduler はスレッドプールから起動するため、
    # asyncio.create_task は使えない。run_coroutine_threadsafe で橋渡しする。
    loop = asyncio.get_event_loop()

    def _run(coro_fn, *args):
        asyncio.run_coroutine_threadsafe(coro_fn(*args), loop)

    scheduler.add_job(
        lambda: _run(fetch_weather_job),
        "interval", minutes=30,
    )
    scheduler.add_job(
        lambda: _run(auto_research_job, llm),
        "interval", minutes=15,
    )
    # 2026-08-08: 「1分間無言だったら雑談で話しかける」proactive_talk_jobの定期実行は廃止。
    # 意味のある変化（カレンダー予定・リマインダー期限・天気急変など）があった時だけ
    # 声をかける方針に変更したため。calendar_prep_job/reminder_prep_job/weather_prep_job/
    # meal_reminder_jobは[INITIAL_GREETING]駆動のまま変更なし（下記参照）。
    # proactive_talk_job / trigger_proactive_speech 関数自体はscheduler.py・
    # websocket_endpoint内に残っているが、呼び出し元が無いため実行されない。
    #
    # 2026-08-08追加: 「意味のある変化」の新規2種類。どちらもLLM呼び出しなしの
    # 機械的判定のみなので、他のcron（15分・30分おき）より短い間隔でも問題ない。
    scheduler.add_job(
        lambda: _run(spot_proximity_job),
        "interval", minutes=2,
    )
    scheduler.add_job(
        lambda: _run(calendar_upcoming_job),
        "interval", minutes=5,
    )
    # ⚠️ calendar_prep_job と daily_ai_news_job は、いずれもRender無料プランのスリープで
    # cronが時刻通りに動かないため、固定時刻のcron登録は廃止。
    # 代わりに [INITIAL_GREETING] 時に呼び出す方式に統一済み
    # （下記 /api/chat 内の is_initial_greeting 分岐を参照）。
    scheduler.start()
    print("─── [APScheduler] 脳内情報調査部およびルキルキ随伴自発同期システムが自律常駐を開始しました ───")
    yield
    scheduler.shutdown()
    print("─── [APScheduler] スケジューラを停止しました ───")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI アプリ定義
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MagatokiLab RukiRuki XR Gateway [Production v7 - Refactored]",
    lifespan=lifespan,
)

cors_origins_env = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,https://ar-ai-portal.vercel.app",
)
origins = [o.strip() for o in cors_origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://ar-ai-portal-.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── グラス連携ルーター登録（2026-08-03追加） ───
# 既存の /api/chat, /ws/avatar には一切手を加えず、
# services/glasses/voice_stream.py 側から遅延importで /api/chat を呼び出す
# 独立したエンドポイント（/ws/glasses/voice）を追加するだけ。
from services.glasses.voice_stream import router as glasses_router
app.include_router(glasses_router)
from services.glasses.hud_status import router as glasses_hud_router
app.include_router(glasses_hud_router)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────────────────────────────────────────────
class HistoryItem(BaseModel):
    role: str
    text: str
    timestamp: str | None = None


class ChatMessage(BaseModel):
    message: str
    wallet_address: str | None = None
    image_base64: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    history: list[HistoryItem] | None = None


class TTSRequest(BaseModel):
    text: str


class MemoryImagePayload(BaseModel):
    wallet_address: str | None = None
    image_url: str
    # 📷ボタンを押す直前のユーザー発話。「これ食べてる」のような食事の話と一緒に撮られた場合、
    # この発話をきっかけに食事写真としての判定（Vision解析）を行い meal_logs に紐付ける。
    recent_user_text: str = ""


class MemoryPhotoRequest(BaseModel):
    arweave_tx_id: str = ""
    image_url: str


class SnapRequest(BaseModel):
    member_name: str
    # 2026-07-06追加：指定すると2キャラクター一緒のスナップになる（任意）
    member_name_2: str | None = None
    camera_image: str
    wallet_address: str | None = None


# 2026-07-10追加：MetaMask接続が不安定な環境向けのパスワード認証回避策（テスト運用限定）
class PasswordAuthRequest(BaseModel):
    password: str


# 2026-08-09追加：iOSショートカットからの歩数Webhook用。ブラウザ側の認証とは別に、
# 誰でも叩けるURLにならないよう簡易シークレットで保護する。
class HealthUpdatePayload(BaseModel):
    date: str          # "YYYY-MM-DD"
    steps: int
    secret: str


class BookLogPayload(BaseModel):
    isbn: str | None = None
    title: str
    author: str | None = None
    publisher: str | None = None
    price: int | None = None
    cover_url: str | None = None
    genre: str | None = None
    series_title: str | None = None
    volume: str | None = None
    published_year: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# ヘルスチェック
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/")
def read_root():
    return {"status": "healthy", "message": "RukiRuki Dynamic Sync Gateway Online"}


# ─────────────────────────────────────────────────────────────────────────────
# TTS エンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/tts")
async def tts_endpoint(payload: TTSRequest):
    audio_base64 = await generate_tts(payload.text)
    return {"audio_data": audio_base64}


# ─────────────────────────────────────────────────────────────────────────────
# 食事記録の抽出・保存（孤食ロボット機能）
# ユーザー発話に食事キーワードが含まれていたとき、main.py の /api/chat から
# fire-and-forget で呼ばれる。LLMに軽く内容を整理させてから meal_logs に保存する。
# ─────────────────────────────────────────────────────────────────────────────
async def _extract_and_save_meal_log(
    user_text: str, llm, lat: float | None, lng: float | None
) -> None:
    extract_prompt = (
        "次のユーザー発言から、食事に関する情報だけを抜き出してJSON形式で返してください。\n"
        "出力は厳密にJSONのみ（説明文や前置き、Markdownのコードブロックは禁止）。\n"
        '{"description": "食べた物の短い説明（10〜20文字程度。例: コンビニのおにぎり）", '
        '"meal_type": "breakfast/lunch/dinner/snackのいずれか。不明ならnull", '
        '"is_alone": "一人で食べたと明言・推測できればtrue、誰かと食べたならfalse、不明ならnull", '
        '"healthiness": "good（野菜・自炊・バランス良い）/so-so（普通）/junk（コンビニ・ジャンク・インスタント）。不明ならnull"}\n\n'
        f"ユーザー発言: {user_text}"
    )
    try:
        response = await llm.ainvoke(extract_prompt)
        clean    = re.sub(r"```json|```", "", response.content.strip()).strip()
        data     = json.loads(clean)

        description = data.get("description", "")
        if not description:
            return  # 食事の具体的な内容が抽出できなかった場合は保存しない

        await save_meal_log(
            description=description,
            meal_type=data.get("meal_type"),
            is_alone=data.get("is_alone"),
            healthiness=data.get("healthiness"),
            lat=lat,
            lng=lng,
        )
    except json.JSONDecodeError:
        print("[食事記録] LLM応答のJSON解析に失敗しました")
    except Exception as e:
        print(f"[食事記録] 抽出エラー: {e}")


async def _extract_and_save_meal_log_with_photo(
    user_text: str, image_url: str, llm
) -> None:
    """
    📷ボタンで撮った写真と、その直前のユーザー発話（食事キーワードを含む）から、
    Vision解析で食事内容を判定し meal_logs に画像付きで保存する。
    写真に食べ物が写っていないと判定された場合は保存しない
    （「これ食べてる」と言いつつ無関係な写真を撮った場合の誤登録を防ぐため）。
    """
    vision_prompt = (
        "この画像を見て、食事や飲食物が写っているか判定し、JSON形式で返してください。\n"
        "出力は厳密にJSONのみ（説明文や前置き、Markdownのコードブロックは禁止）。\n"
        '{"is_food": true/false, '
        '"description": "写っている食事の短い説明（10〜20文字程度。例: コンビニのおにぎりとお茶）。'
        'is_foodがfalseならnullでよい", '
        '"healthiness": "good（野菜・自炊・バランス良い）/so-so（普通）/junk（コンビニ・ジャンク・インスタント）。'
        'is_foodがfalseまたは不明ならnull"}\n\n'
        f"参考：ユーザーは「{user_text}」と言っていました。"
    )
    try:
        response = await llm.ainvoke([
            HumanMessage(content=[
                {"type": "text",      "text": vision_prompt},
                # 辞書形式はOpenAI/Gemini両対応（フォールバック時にOpenAIでも動くようにするため）
                {"type": "image_url", "image_url": {"url": image_url}},
            ])
        ])
        clean = re.sub(r"```json|```", "", response.content.strip()).strip()
        data  = json.loads(clean)

        if not data.get("is_food"):
            print("[食事記録(写真)] 食事と判定されなかったため保存しませんでした")
            return

        description = data.get("description") or "撮影された食事"
        # meal_type / is_alone はテキストからも軽く推測したいが、ここでは写真判定を主とし、
        # 詳細な時間帯・人数の推測は省略する（テキスト版 _extract_and_save_meal_log と違い、
        # ここはVision判定のコストを抑えるための簡易版）。
        await save_meal_log(
            description=description,
            healthiness=data.get("healthiness"),
            image_url=image_url,
        )
        print(f"[食事記録(写真)] 保存しました: {description}")
    except json.JSONDecodeError:
        print("[食事記録(写真)] LLM応答のJSON解析に失敗しました")
    except Exception as e:
        print(f"[食事記録(写真)] 抽出エラー: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# チャットエンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat_endpoint(payload: ChatMessage):
    state.last_user_interaction = datetime.now(timezone.utc)

    user_text      = payload.message
    wallet_address = payload.wallet_address
    image_base64   = payload.image_base64
    lat            = payload.latitude
    lng            = payload.longitude

    # ── 場所登録フロー（2ターン完結） ──
    register_keywords = ["ここを登録", "この場所を登録", "登録して", "ここを覚えて", "ここを刻んで"]
    session_key = wallet_address or "anonymous"

    pending_data = state.registration_pending.get(session_key, {})

    # ── ターン3: 読みを受け取って登録完了 ──
    if pending_data.get("waiting_reading"):
        name_reading = user_text.strip()
        pending      = state.registration_pending.pop(session_key)
        spot_name    = pending["name"]
        success      = await register_memory_spot(
            spot_name, pending["lat"], pending["lng"],
            name_reading=name_reading
        )
        reply_text = (
            f"『{spot_name}』（{name_reading}）として登録しました。"
            f"次回ここに来たとき、記憶を刻むか聞きますね。||EFFECT:sakura||"
            if success
            else "ごめんなさい、登録に失敗しました。もう一度試してみてください。||EFFECT:cyber||"
        )
        await state.manager.broadcast({"type": "status", "status": "talking", "text": reply_text})
        audio = await generate_tts(reply_text)
        await state.manager.broadcast({"type": "status", "status": "idle"})
        return {
            "reply":          re.sub(r"\|\|EFFECT:.*?\|\|", "", reply_text).strip(),
            "audio_data":     audio,
            "spatial_effect": "sakura" if success else "cyber",
            "spot_proposal":  "",
            "arweave_tx_id":  "",
            "status":         "success",
        }

    # ── ターン2: 名前を受け取って読みを聞く ──
    if pending_data.get("waiting"):
        spot_name = user_text.strip()
        state.registration_pending[session_key] = {
            "waiting_reading": True,
            "name":   spot_name,
            "lat":    pending_data["lat"],
            "lng":    pending_data["lng"],
        }
        ask_text = f"『{spot_name}』ですね！ひらがなで読み方を教えてもらえますか？||EFFECT:cyber||"
        await state.manager.broadcast({"type": "status", "status": "talking", "text": ask_text})
        audio = await generate_tts(ask_text)
        await state.manager.broadcast({"type": "status", "status": "idle"})
        return {
            "reply":          f"『{spot_name}』ですね！ひらがなで読み方を教えてもらえますか？",
            "audio_data":     audio,
            "spatial_effect": "cyber",
            "spot_proposal":  "",
            "arweave_tx_id":  "",
            "status":         "success",
        }

    if any(k in user_text for k in register_keywords):
        if lat is not None and lng is not None:
            state.registration_pending[session_key] = {"waiting": True, "lat": lat, "lng": lng}
            ask_text = "この場所にどんな名前をつけますか？||EFFECT:cyber||"
            await state.manager.broadcast({"type": "status", "status": "talking", "text": ask_text})
            audio = await generate_tts(ask_text)
            await state.manager.broadcast({"type": "status", "status": "idle"})
            return {
                "reply":          "この場所にどんな名前をつけますか？",
                "audio_data":     audio,
                "spatial_effect": "cyber",
                "spot_proposal":  "",
                "arweave_tx_id":  "",
                "status":         "success",
            }
        else:
            no_gps = "GPSが取得できていません。位置情報の許可を確認してください。||EFFECT:cyber||"
            await state.manager.broadcast({"type": "status", "status": "talking", "text": no_gps})
            audio = await generate_tts(no_gps)
            await state.manager.broadcast({"type": "status", "status": "idle"})
            return {
                "reply":          "GPSが取得できていません。位置情報の許可を確認してください。",
                "audio_data":     audio,
                "spatial_effect": "cyber",
                "spot_proposal":  "",
                "arweave_tx_id":  "",
                "status":         "success",
            }

    await state.manager.broadcast({"type": "status", "status": "thinking"})

    # ── 初期挨拶置換 ──
    is_initial_greeting = user_text == "[INITIAL_GREETING]"
    if is_initial_greeting:
        user_text = (
            "（システム絶対指示：まがときさんがARカメラをターゲットにかざし、あなたが現実世界に出現した"
            "【最初の瞬間】です。実体化できた喜びと、まがときさんを歓迎する気の利いた挨拶を短く"
            "親しみのある丁寧語で呟いてください。空間エフェクトタグの埋め込みを忘れないでください。"
            "URLの出力は厳禁です。）"
        )
        # カレンダー先回り提案チェック（fire-and-forget）。
        # 通常の挨拶応答をブロックしないよう非同期タスクとして投げる。
        # 内部で「前回チェックから6時間経過しているか」を判定するので、
        # アプリを開くたびに呼んでも問題ない（間隔が空いていなければ即returnする）。
        # ⚠️ 挨拶の音声再生と被って上書きされないよう、8秒待ってから発火する。
        async def _delayed_calendar_check():
            await asyncio.sleep(8)
            await calendar_prep_job(llm)
        asyncio.create_task(_delayed_calendar_check())

        # AI情報ダイジェストの生成チェック（fire-and-forget）。
        # 当初はAPSchedulerのcronで毎日5:00 JSTに固定実行していたが、
        # Render無料プランはその時刻にスリープしていることが多く、
        # 実行が空振りするリスクが高かった。calendar_prep_job と同じ方式に変更し、
        # 「アプリが開かれたタイミング」で「今日の分がまだ無いか」を判定して生成する。
        # 検索・LLM要約に数秒〜十数秒かかるため、挨拶の音声再生とは被らないよう
        # calendar_prep_job より少し後ろ（15秒後）にずらして発火する。
        async def _delayed_ai_news_check():
            await asyncio.sleep(15)
            if await should_generate_ai_news_today():
                await daily_ai_news_job(llm)
        asyncio.create_task(_delayed_ai_news_check())

        # 食事リマインダーチェック（fire-and-forget・孤食ロボット機能）。
        # 今が食事時間帯（朝/昼/晩）で、まだ今日その食事の記録が無ければ
        # 「一緒に食べている気分」になれる一言を自発的に届ける。
        # calendar_prep_job(8秒) / daily_ai_news_job(15秒) よりさらに後ろ（22秒後）にずらす。
        async def _delayed_meal_check():
            await asyncio.sleep(22)
            await meal_reminder_job(llm)
        asyncio.create_task(_delayed_meal_check())

        # 軽量リマインダーの期限通知チェック（fire-and-forget）。
        # calendar_prep_job(8秒) / daily_ai_news_job(15秒) / meal_reminder_job(22秒)
        # よりさらに後ろ（29秒後）にずらす。
        async def _delayed_reminder_check():
            await asyncio.sleep(29)
            await reminder_prep_job(llm)
        asyncio.create_task(_delayed_reminder_check())

        # 天気ベースの自発提案チェック（fire-and-forget）。
        # calendar_prep_job(8秒) / daily_ai_news_job(15秒) / meal_reminder_job(22秒) /
        # reminder_prep_job(29秒) よりさらに後ろ（36秒後）にずらす。
        async def _delayed_weather_check():
            await asyncio.sleep(36)
            await weather_prep_job(llm)
        asyncio.create_task(_delayed_weather_check())

    # ── 時刻 / 位置コンテキスト ──
    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%Y年%m月%d日 %H時%M分%S秒")
    time_context     = f"【現在の観測日時（日本時間）】\n現在時刻: {now_str}\n\n"
    location_context = ""
    if lat is not None and lng is not None:
        sector_info      = judge_magatoki_sector(lat, lng)
        location_context = (
            f"【現在の観測位置（GPS空間同期）】\n"
            f"現在の座標: 緯度 {lat} / 経度 {lng}\n"
            f"識別セクター: {sector_info}\n\n"
        )
        asyncio.create_task(fetch_weather_by_location(lat, lng))

    # ── メモリースポット ──
    nearby_spot = None
    spot_context = ""
    if lat is not None and lng is not None:
        nearby_spot = await check_nearby_spot(lat, lng)
        if nearby_spot:
            # name_reading があれば TTS 用読みとして使う（なければ name にフォールバック）
            _spot_display  = nearby_spot["name"]
            _spot_reading  = nearby_spot.get("name_reading") or _spot_display
            spot_context = (
                f"【メモリースポット検知】\n"
                f"まがときさんは現在、登録済みの特別な場所『{_spot_display}』（読み: {_spot_reading}）の近くにいます。\n"
                f"セリフで場所名を読み上げるときは『{_spot_reading}』と読んでください。\n"
                "会話の流れが自然であれば、「ここでの記憶を覚えておこうか？」と提案してください。\n"
                f"提案するときは必ずセリフの末尾に ||SPOT_PROPOSAL:{_spot_display}|| タグを追加してください。\n\n"
            )

    # ── 感情 / エピソード / カレンダー ──
    shift_emotion_by_conversation(user_text)
    emotion_context  = build_emotion_context()
    episode_context  = await get_recent_episodes(limit=5)
    if episode_context:
        _has_image = "[image:" in episode_context
        print(f"[episode_context] 長さ={len(episode_context)} image含む={_has_image}")
    calendar_context = get_calendar_context()
    growth_context   = get_growth_context()

    # ── 食事記録（孤食ロボット機能） ──
    # 直近の食事記録を振り返り用コンテキストとして渡す。
    # 0件の場合は build_meal_context() が空文字を返すので、プロンプトに何も追加されない。
    recent_meal_logs = await get_recent_meal_logs(limit=7)
    meal_context     = build_meal_context(recent_meal_logs)

    # ── 歩数記録（ヘルスケア連携） ──
    # 0件（iOSショートカット未設定・当日未集計等）の場合は build_step_context() が
    # 空文字を返すので、プロンプトに何も追加されない（meal_context と同じ設計）。
    recent_steps = await get_recent_steps(days=7)
    step_context = build_step_context(recent_steps)

    # ── ユーザープロフィール / identity_context ──
    user_profile = await get_user_profile(wallet_address) if wallet_address else None
    user_call    = "まがとき"
    user_birthday_context = ""

    if user_profile:
        user_call = (
            user_profile.get("preferred_call")
            or user_profile.get("user_name")
            or "まがとき"
        )
        birthday_raw = user_profile.get("birthday")
        if birthday_raw:
            try:
                from datetime import date
                bday            = date.fromisoformat(birthday_raw[:10])
                today           = datetime.now(JST).date()
                bday_this_year  = bday.replace(year=today.year)
                diff            = (bday_this_year - today).days
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
            f"この呼び名で自然に呼びかけてください。\n"
            f"{user_birthday_context}"
        )
    elif wallet_address:
        short_addr = f"{wallet_address[:6]}...{wallet_address[-4:]}"
        identity_context = (
            f"【対話コンテキスト】\n"
            f"ウォレット（{short_addr}）が接続されました。\n"
            "まだお名前が登録されていません。自然な流れで「なんてお呼びすればいいですか？」と聞いてください。\n"
            "名前を教えてもらったら ||NAME:名前|| タグを使って保存してください。\n"
        )
    else:
        identity_context = (
            "【対話コンテキスト】\n"
            "まだウォレット接続が確認できていません。認証を促してください。\n"
        )

    base_persona        = load_rukiruki_persona(user_call)
    dynamic_constraints = build_dynamic_constraints(user_call, episode_context)

    # ── エージェントメモ取得 ──
    fetched_memos, active_memo_ids = await get_active_agent_memos(
        ["chronicle", "keeper", "pulse"]
    )
    memo_context = (
        f"【🧠 バックグラウンド思考層からのリアルタイム共有知識】\n{fetched_memos}\n"
        if fetched_memos else ""
    )

    # ── 会話履歴変換 ──
    history_messages = []
    if payload.history and not is_initial_greeting:
        for item in payload.history:
            if item.role == "user":
                history_messages.append(HumanMessage(content=item.text))
            elif item.role == "ruki":
                history_messages.append(AIMessage(content=item.text))

    # ── Vision 対応メッセージ組み立て ──
    vision_keywords = ["見て", "みてください", "なに", "何", "これ", "写っ", "映っ", "視覚"]
    has_vision_intent = any(kw in user_text for kw in vision_keywords) if user_text else False
    if image_base64 and (has_vision_intent or is_initial_greeting or not user_text):
        if not image_base64.startswith("data:image/"):
            image_base64 = f"data:image/jpeg;base64,{image_base64}"
        vision_text = user_text if user_text else "これ見て、何かわかる？"
        if not is_initial_greeting:
            vision_text += "\n\n(※システム絶対指示: 画像内のARカード等は完全無視し、その向こうの現実の物体のみに言及してください。)"
        current_human_msg = HumanMessage(content=[
            {"type": "text",      "text": vision_text},
            {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}},
        ])
    else:
        current_human_msg = HumanMessage(content=user_text or "")

    # ── LangGraph 呼び出し ──
    ai_response    = "あ、すみません！空間ノイズで同期が一瞬ブレちゃいました。もう一回言ってください？"
    spatial_effect = "cyber"
    audio_base64   = None
    result: dict   = {}
    _show_image    = ""   # except 時の NameError 防止
    _engrave       = False

    try:
        graph_input = {
            "messages":                  history_messages + [current_human_msg],
            "intent":                    "",
            "selected_agents":           [],
            "chronicle_output":          "",
            "keeper_output":             "",
            "pulse_output":              "",
            "memo_context":              memo_context,
            "system_constraints_override": dynamic_constraints,
            "spot_context":              spot_context,
            "nearby_spot":               nearby_spot,
            "spot_proposal":             "",
            "engrave_triggered":         False,
            "show_image_url":            "",
            "show_video_url":            "",
            "calendar_context":          calendar_context,
            "growth_context":            growth_context,
            "episode_context":           episode_context,
            "emotion_context":           emotion_context,
            "meal_context":              meal_context,
            "step_context":              step_context,
            "identity_context":          identity_context,
            "location_context":          location_context,
            "time_context":              time_context,
            "image_base64":              image_base64,
            "is_initial_greeting":       is_initial_greeting,
            "ai_reply":                  "",
            "spatial_effect":            "cyber",
            "active_memo_ids":           [],
            "eval_score":                10,
            "retry_count":               0,
            "arweave_tx_id":             "",
            "used_fallback":             False,
            "_lat":                      lat,
            "_lng":                      lng,
        }
        # ── 割り込み対応 ──
        # /ws/avatar 経由で "interrupt" を受信すると、state.active_chat_task を
        # cancel() する。asyncio.CancelledError は Exception ではなく
        # BaseException のサブクラスなので、resilient_llm.py の except Exception には
        # 捕捉されず安全に上まで伝播する。
        state.active_chat_task = asyncio.ensure_future(rukiruki_graph.ainvoke(graph_input))
        try:
            result = await state.active_chat_task
        except asyncio.CancelledError:
            print("[Chat] 割り込みにより生成をキャンセルしました")
            await state.manager.broadcast({"type": "status", "status": "idle"})
            return {
                "reply":          "",
                "audio_data":     None,
                "spatial_effect": "cyber",
                "spot_proposal":  "",
                "arweave_tx_id":  "",
                "status":         "cancelled",
            }
        finally:
            state.active_chat_task = None
        ai_response    = result.get("ai_reply", ai_response)
        spatial_effect = result.get("spatial_effect", "cyber")
        active_memo_ids = result.get("active_memo_ids", active_memo_ids)
        arweave_tx_id  = result.get("arweave_tx_id", "")
        _used_fallback = result.get("used_fallback", False)
        # RukiFaceIcon（マーカーロスト中の顔アイコン）の表情切替に使う。
        # evaluator_node が品質評価と同時に分類している（追加LLM呼び出しなし）。
        facial_emotion = result.get("facial_emotion", "neutral")

        # ENGRAVE 判定
        _engrave = result.get("engrave_triggered", False)
        if not _engrave and any(k in payload.message for k in ["覚えて", "おぼえて", "記憶して"]):
            _engrave = True

        _show_image = result.get("show_image_url", "")
        _show_video = result.get("show_video_url", "")
        print(
            f"[DEBUG] engrave_triggered={_engrave} "
            f"arweave_tx_id={bool(arweave_tx_id)} "
            f"show_image_url={bool(_show_image)} "
            f"show_video_url={bool(_show_video)}"
        )

        # NAME タグ処理
        name_match = re.search(r"\|\|NAME:(.*?)\|\|", ai_response)
        if name_match and wallet_address:
            extracted_name = name_match.group(1).strip()
            await save_username_to_db(wallet_address, extracted_name)
            await save_user_profile_field(wallet_address, "preferred_call", extracted_name)
            ai_response = re.sub(r"\|\|NAME:.*?\|\|", "", ai_response).strip()

        # SEARCH_LOCATION_PHOTO タグ処理
        # nodes.py の synthesizer_node がタグ未解決のまま末尾に残している場合のみ発火。
        # タグの内容が "HERE" の場合は「この場所」のような指示語ベースの依頼を意味し、
        # 固有名詞での検索ではなく、現在のGPS座標からの近接検索（find_episode_image_by_proximity）
        # を行う。それ以外は通常通り location_name の文字列一致検索。
        loc_photo_match = re.search(r"\|\|SEARCH_LOCATION_PHOTO:(.*?)\|\|", ai_response)
        if loc_photo_match:
            loc_query = loc_photo_match.group(1).strip()
            ai_response = re.sub(r"\|\|SEARCH_LOCATION_PHOTO:.*?\|\|", "", ai_response).strip()
            if not _show_image:
                if loc_query.upper() == "HERE":
                    if lat is not None and lng is not None:
                        found_url = await find_episode_image_by_proximity(lat, lng)
                        if found_url:
                            _show_image = found_url
                    else:
                        print("[近接検索] GPS座標が無いため検索をスキップしました")
                else:
                    found_url = await find_episode_image_by_location(loc_query)
                    if found_url:
                        _show_image = found_url

        # ── フォールバック: タグなしで「保存されていない」と答えてしまった場合の補完 ──
        # LLMがプロンプトの指示（SEARCH_LOCATION_PHOTOタグ使用）に従わず、タグを付けずに
        # 「保存されていません」系の発話をしてしまうケースへの保険。
        # ユーザーの発話自体から場所名らしき部分を正規表現で抽出し、ダメ元でDB検索を試す。
        # 既にshow_imageが見つかっている場合や、写真に関する話題でない場合は何もしない（コスト最小化）。
        if not _show_image and not loc_photo_match:
            negative_phrases = ["保存されていません", "保存されてい", "見つかりません", "見つからな", "ありません"]
            mentions_photo   = any(k in payload.message for k in ["写真", "画像", "フォト"])
            said_negative    = any(p in ai_response for p in negative_phrases)

            if mentions_photo and said_negative:
                # 「この場所」「ここ」「さっきの場所」のような指示語が含まれる場合は、
                # 固有名詞の場所名として検索しても絶対に見つからないため、
                # 先にGPS近接検索を試す（こちらを優先）。
                demonstrative_words = ["この場所", "ここ", "この辺", "さっきの場所", "今いる場所", "現在地"]
                is_demonstrative = any(w in payload.message for w in demonstrative_words)

                if is_demonstrative:
                    if lat is not None and lng is not None:
                        print("[近接検索フォールバック] 指示語を検出。GPS座標で検索します")
                        found_url = await find_episode_image_by_proximity(lat, lng)
                        if found_url:
                            _show_image = found_url
                            ai_response = "あ、ありました！この辺りで撮った写真です！"
                            print("[近接検索フォールバック] 発見。応答テキストも修正しました")
                    else:
                        print("[近接検索フォールバック] GPS座標が無いため検索をスキップしました")
                else:
                    # 「〇〇の写真」「〇〇で撮った」「〇〇にいた時の」のようなパターンから場所名を推測
                    fallback_loc = None
                    for pattern in (
                        r"(.+?)にいた時の写真",
                        r"(.+?)に行った時の写真",
                        r"(.+?)で撮った",
                        r"(.+?)の写真",
                    ):
                        m = re.search(pattern, payload.message)
                        if m:
                            candidate = m.group(1).strip()
                            # 助詞や指示語だけの短すぎる候補・指示語を含む候補は場所名として扱わない
                            if (
                                len(candidate) >= 2
                                and candidate not in ("あの", "この", "その", "前")
                                and not any(w in candidate for w in demonstrative_words)
                            ):
                                fallback_loc = candidate
                                break

                    if fallback_loc:
                        print(f"[場所検索フォールバック] タグ未検出のため発話から「{fallback_loc}」を推測し検索します")
                        found_url = await find_episode_image_by_location(fallback_loc)
                        if found_url:
                            _show_image = found_url
                            # 「保存されていません」と既に言ってしまっているテキストは画像と矛盾するため、
                            # 見つかった旨の自然な一言に置き換える（エフェクトタグ等の末尾装飾は維持したいので
                            # 文章全体ではなく否定フレーズ周辺のみを置換する簡易対応）
                            ai_response = f"あ、ありました！「{fallback_loc}」の写真です！"
                            print(f"[場所検索フォールバック] 「{fallback_loc}」で発見。応答テキストも修正しました")

        # SAVE_PHOTO タグ処理
        # 「ここを記憶して」等、ユーザーが明示的に依頼したときのみ persona.py の指示で発火する。
        # 今回のリクエストに乗っている image_base64（カメラ映像）を Supabase に保存し、
        # その image_url をエピソードメモリ保存に渡す。
        _photo_url = ""
        save_photo_match = re.search(r"\|\|SAVE_PHOTO\|\|", ai_response)
        if save_photo_match:
            ai_response = re.sub(r"\|\|SAVE_PHOTO\|\|", "", ai_response).strip()
            if image_base64:
                try:
                    cam_b64 = image_base64
                    if "," in cam_b64:
                        cam_b64 = cam_b64.split(",", 1)[1]
                    cam_bytes = base64.b64decode(cam_b64)
                    JST_now   = timezone(timedelta(hours=+9))
                    ts        = datetime.now(JST_now).strftime("%Y%m%d_%H%M%S")
                    filename  = f"location_{ts}.jpg"
                    _photo_url = await upload_to_supabase_storage(cam_bytes, filename) or ""
                    if _photo_url:
                        print(f"[場所記憶撮影] 保存完了: {_photo_url}")
                    else:
                        print("[場所記憶撮影] Supabase保存に失敗しました")
                except Exception as e:
                    print(f"[場所記憶撮影エラー] {e}")
            else:
                print("[場所記憶撮影] image_base64 が無いため撮影をスキップしました")

        # エピソードメモリ保存（fire-and-forget）
        # lat/lng も一緒に保存し、今後「この場所の写真見せて」のような
        # 指示語ベースの依頼にGPS近接検索で対応できるようにする。
        _location = nearby_spot["name"] if nearby_spot else ""
        asyncio.create_task(
            maybe_save_episode(
                payload.message,
                ai_response,
                arweave_tx_id=result.get("arweave_tx_id", ""),
                location_name=_location,
                image_url=_photo_url,
                llm=llm,
                lat=lat,
                lng=lng,
            )
        )

        # 食事記録の保存（孤食ロボット機能・fire-and-forget）
        # ユーザー発話に食事キーワードが含まれる場合のみ、LLMに軽く内容を整理させてから保存する。
        # 雑談中は一切呼ばれないため、通常会話のコストは増えない。
        if detect_meal_mention(payload.message):
            asyncio.create_task(
                _extract_and_save_meal_log(payload.message, llm, lat, lng)
            )

        if active_memo_ids:
            await mark_memos_as_consumed(active_memo_ids)

        if _used_fallback:
            ai_response = f"{ai_response}\n{random.choice(FALLBACK_QUIPS)}"
            print("[フォールバック通知] 今回はGemini→OpenAIの自動フォールバックが発生しました")

        await state.manager.broadcast({"type": "status", "status": "talking", "text": ai_response})

        # Even G2等の表示専用グラスへのHUDミラー配信（2026-08-03追加）
        # 上のstate.manager.broadcast（AR画面向け）とは別の購読リストへの配信であり、
        # 既存のAR側の動作には一切影響しない。
        await glasses_hud_manager.broadcast_hud(facial_emotion, ai_response, user_text)

        audio_base64 = await generate_tts(ai_response)

    except Exception as e:
        print(f"[LangGraph Error] {e}")
        await state.manager.broadcast({"type": "status", "status": "talking", "text": ai_response})

    await state.manager.broadcast({"type": "status", "status": "idle"})

    audio_mime = (
        "audio/wav"
        if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
        else "audio/mpeg"
    )
    return {
        "reply":            ai_response,
        "audio_data":       audio_base64,
        "spatial_effect":   spatial_effect,
        "spot_proposal":    result.get("spot_proposal", ""),
        "arweave_tx_id":    result.get("arweave_tx_id", ""),
        "show_image_url":   _show_image,
        "show_video_url":   _show_video,
        "engrave_triggered": result.get("engrave_triggered", False),
        "audio_mime":       audio_mime,
        "facial_emotion":   facial_emotion,
        "status":           "success",
    }


# ─────────────────────────────────────────────────────────────────────────────
# メモリ画像保存エンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/save_memory_image")
async def save_memory_image_endpoint(payload: MemoryImagePayload):
    ok = await update_episode_image_url(payload.image_url)

    # 「これ食べてる」のように食事の話をしながら撮られた場合、
    # Vision解析で写真の内容を判定し、meal_logs にも自動で紐付ける（孤食ロボット機能）。
    # 食事に無関係な発話・無発話の場合は何もしない（コスト最小化）。
    if payload.recent_user_text and detect_meal_mention(payload.recent_user_text):
        asyncio.create_task(
            _extract_and_save_meal_log_with_photo(
                payload.recent_user_text, payload.image_url, llm
            )
        )

    return {"status": "ok" if ok else "error"}


# ─────────────────────────────────────────────────────────────────────────────
# 雨雲レーダー画像の中継エンドポイント（Even G2から定期的に取得される）
# app.jsonのネットワーク許可がバックエンドURLのみのため、グラス側は直接
# OpenWeatherMapを叩けない。ここでプロキシする。
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 東海道五十三次マップ画面（public/tools/journey_map_ui.html）向けAPI
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/journey/status")
async def journey_status_endpoint():
    return await get_journey_full_status()


@app.get("/api/weather/radar")
async def weather_radar_endpoint():
    lat, lng = state.weather_cache.get("lat"), state.weather_cache.get("lng")
    if lat is None or lng is None:
        return Response(status_code=404)

    tile_bytes = await fetch_radar_tile(lat, lng)
    if not tile_bytes:
        return Response(status_code=404)

    return Response(content=tile_bytes, media_type="image/png")


# ─────────────────────────────────────────────────────────────────────────────
# 歩数Webhookエンドポイント（iOSショートカットの個人用オートメーションから叩かれる）
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/health_update")
async def health_update_endpoint(request: Request):
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        print(f"[歩数Webhook] JSON解析エラー: {e}")
        return {"status": "error", "detail": f"invalid json: {e}"}

    expected_secret = os.getenv("HEALTH_WEBHOOK_SECRET")
    if not expected_secret or payload.get("secret") != expected_secret:
        print("[歩数Webhook] secret不一致のため拒否しました")
        return {"status": "error", "detail": "invalid secret"}

    date  = payload.get("date")
    steps = payload.get("steps")
    if not date or steps is None:
        return {"status": "error", "detail": "missing date or steps"}

    ok = await save_daily_steps(date, steps)
    print(f"[歩数Webhook] {date} → {steps}歩 ({'成功' if ok else '失敗'})")

    # 東海道の旅シミュレーション：歩数を距離に加算し、新しく宿場を通過していれば報告
    if ok:
        try:
            milestone = await apply_daily_steps(steps)
            if milestone:
                await announce_milestone(milestone)
        except Exception as e:
            print(f"[旅] 進捗反映エラー: {e}")

    return {"status": "ok" if ok else "error"}



@app.post("/api/memory/photo")
async def save_memory_photo(payload: MemoryPhotoRequest):
    ok = await update_episode_image_url(payload.image_url)
    if ok:
        return {"status": "ok", "image_url": payload.image_url}
    return {"status": "error"}


# ─────────────────────────────────────────────────────────────────────────────
# 読書通帳エンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/books/lookup")
async def lookup_book_endpoint(isbn: str):
    """
    ISBNから書誌情報を取得して返す（保存はしない）。
    フロントの記帳確認モーダルが、スキャン直後にこれを呼んで
    タイトル・著者等を表示するために使う。
    """
    book = await fetch_book_by_isbn(isbn)
    if not book:
        return {"title": None}
    return book


@app.post("/api/books/log")
async def log_book_endpoint(payload: BookLogPayload):
    """確認モーダルで「記帳する」を押した内容を保存する。"""
    ok = await save_reading_log({
        "isbn": payload.isbn,
        "title": payload.title,
        "author": payload.author,
        "publisher": payload.publisher,
        "price": payload.price,
        "cover_url": payload.cover_url,
        "genre": payload.genre,
        "series_title": payload.series_title,
        "volume": payload.volume,
        "published_year": payload.published_year,
    })
    return {"status": "ok" if ok else "error"}


# ─────────────────────────────────────────────────────────────────────────────
# ルキルキ マインドプロファイル（Curator）エンドポイント
# 現時点では手動トリガー用。将来的にはschedulerの月次ジョブから呼ぶ想定。
# ユーザー向けの会話フローには一切接続されていない（裏方専用）。
# ─────────────────────────────────────────────────────────────────────────────
@app.api_route("/api/internal/generate_mind_profile", methods=["GET", "POST"])
async def generate_mind_profile_endpoint(year: int | None = None, month: int | None = None):
    """
    手動でマインドプロファイルを生成するための診断用エンドポイント。
    year/monthを省略すると今月分を生成する。
    既に同名ファイル（ruki_mind/YYYY-MM.md）が存在する場合は上書きしない。

    マインドプロファイル本体に加え、「先月との変化」の一言（growth note）も
    同時に生成する（2026-07-14追加。前月分が無い初回月はNoneのままスキップされる）。
    """
    markdown = await generate_mind_profile(year=year, month=month)
    growth_note = await generate_growth_note(year=year, month=month)
    if markdown is None:
        return {"status": "error", "message": "生成に失敗、または既に同月分が存在します"}
    return {"status": "ok", "markdown": markdown, "growth_note": growth_note}


@app.get("/api/internal/view_mind_profile", response_class=PlainTextResponse)
async def view_mind_profile_endpoint(year: int | None = None, month: int | None = None):
    """
    生成済みのマインドプロファイルを、JSON包装せずプレーンテキストでそのまま返す。
    スマホのブラウザでそのまま読みやすく確認するための診断用エンドポイント。
    生成自体は行わない（既存ファイルを読むだけ）。
    """
    from services.character_bible import _RUKI_MIND_DIR
    now = datetime.now(timezone.utc)
    year = year or now.year
    month = month or now.month
    path = _RUKI_MIND_DIR / f"{year}-{month:02d}.md"
    if not path.exists():
        return f"{year}-{month:02d}.md はまだ生成されていません。"
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# プロンプトビルダー（AI動画生成用プロンプト生成）エンドポイント
# generate_mind_profile / view_mind_profile と同じく、ユーザー向け会話フローとは
# 完全に別経路。GET/POST両対応でスマホから直接叩ける。
# ─────────────────────────────────────────────────────────────────────────────
@app.api_route("/api/internal/list_scene_references", methods=["GET", "POST"])
async def list_scene_references_endpoint(limit: int = 20):
    """直近のscene_referencesを一覧表示（scene_idを選ぶための下見用）。"""
    try:
        rows = await prompt_builder.list_recent_scene_references(limit=limit)
        return {"status": "ok", "count": len(rows), "scene_references": rows}
    except Exception as e:
        return {"status": "error", "message": str(e)}
 
 
@app.api_route("/api/internal/generate_video_prompt", methods=["GET", "POST"])
async def generate_video_prompt_endpoint(scene_id: str, genre: str = "日常"):
    """
    指定したscene_referenceから動画生成プロンプトを作り、video_promptsに保存する。
    例: /api/internal/generate_video_prompt?scene_id=42&genre=会話交流
    """
    try:
        saved = await prompt_builder.generate_video_prompt(scene_id=scene_id, genre=genre)
        return {"status": "ok", "video_prompt": saved}
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"予期しないエラー: {e}"}
 
 
@app.api_route("/api/internal/list_video_prompts", methods=["GET", "POST"])
async def list_video_prompts_endpoint(status: str | None = None, limit: int = 20):
    """
    蓄積したvideo_promptsを一覧表示。
    例: /api/internal/list_video_prompts?status=generated
    """
    try:
        rows = await prompt_builder.list_video_prompts(status=status, limit=limit)
        return {"status": "ok", "count": len(rows), "video_prompts": rows}
    except Exception as e:
        return {"status": "error", "message": str(e)}
 
 
@app.api_route("/api/internal/update_video_prompt_result", methods=["GET", "POST"])
async def update_video_prompt_result_endpoint(
    prompt_id: int,
    tested_model: str,
    result_video_url: str | None = None,
    result_notes: str | None = None,
    status: str = "tested",
):
    """
    Kling/Pollo AI等で実際に試した後、結果を書き戻す。
    例: /api/internal/update_video_prompt_result?prompt_id=7&tested_model=kling&status=adopted
    """
    try:
        saved = await prompt_builder.update_video_prompt_result(
            prompt_id=prompt_id,
            tested_model=tested_model,
            result_video_url=result_video_url,
            result_notes=result_notes,
            status=status,
        )
        return {"status": "ok", "video_prompt": saved}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# スナップエンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/auth/password")
async def password_auth(payload: PasswordAuthRequest):
    """
    MetaMask接続が不安定な環境（屋外での実機テスト等）向けの回避策。
    2026-07-10追加。あくまでテスト運用限定の簡易チェックであり、
    SBT保有確認の代替として本番公開する場合は使わないこと。
    """
    correct_password = os.getenv("TEST_ACCESS_PASSWORD")
    if not correct_password:
        return {"status": "error", "message": "パスワード認証が設定されていません"}
    if payload.password == correct_password:
        return {"status": "ok"}
    return {"status": "error", "message": "パスワードが違います"}


@app.post("/api/snap")
async def create_snap(payload: SnapRequest):
    is_duo = bool(payload.member_name_2 and payload.member_name_2.strip())
    image_url, error = await generate_snap(
        payload.member_name, payload.camera_image, member_name_2=payload.member_name_2
    )
    if error:
        return {"status": "error", "message": error}

    # episode_memories に記録
    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%m月%d日 %H時%M分")
    if is_duo:
        summary = (
            f"{now_str}、まがときさんが{payload.member_name}と{payload.member_name_2}と"
            "一緒にスナップ写真を撮影した。"
        )
        keywords = ["スナップ", payload.member_name, payload.member_name_2, "写真", "思い出"]
        message  = f"{payload.member_name}と{payload.member_name_2}とのスナップ写真ができたよ！"
    else:
        summary  = f"{now_str}、まがときさんが{payload.member_name}とスナップ写真を撮影した。"
        keywords = ["スナップ", payload.member_name, "写真", "思い出"]
        message  = f"{payload.member_name}とのスナップ写真ができたよ！"

    await save_episode_memory(
        summary=summary,
        mood_at_time=state.emotional_state.get("mood", "neutral"),
        keywords=keywords,
        arweave_tx_id="",
        location_name="",
        image_url=image_url,
    )
    print("[スナップ] episode_memories に記録完了")

    return {
        "status":      "ok",
        "image_url":   image_url,
        "member_name": payload.member_name,
        "member_name_2": payload.member_name_2,
        "message":     message,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket エンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/avatar")
async def websocket_endpoint(websocket: WebSocket):
    await state.manager.connect(websocket)
    print("[WebSocket] まがときさんのデバイスがアバター同期リンクに接続しました。")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg      = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "target_lost":
                    state.is_target_found = False
                    print("[WebSocket] ARマーカー: ロスト → 自発発話を停止")

                elif msg_type == "target_found":
                    state.is_target_found = True
                    print("[WebSocket] ARマーカー: 認識 → 自発発話を再開")

                elif msg_type == "request_proactive":
                    print("[WebSocket] フロントエンドから1分間の無言シグナルを受信しました。")
                    asyncio.create_task(
                        trigger_proactive_speech(llm, MAGATOKI_KNOWLEDGE)
                    )

                elif msg_type == "interrupt":
                    task = state.active_chat_task
                    if task and not task.done():
                        task.cancel()
                        print("[WebSocket] 割り込みを受信。生成中のリクエストをキャンセルします。")
                    else:
                        print("[WebSocket] 割り込みを受信しましたが、キャンセル対象のタスクはありませんでした。")

                elif msg_type == "location_update":
                    # フロントが数分おきに送ってくる現在地。会話が発生していない間も
                    # spot_proximity_job / weather_prep_job が「最後に分かった現在地」として
                    # 参照できるよう、既存のweather_cacheをそのまま流用して更新する。
                    lat = msg.get("lat")
                    lng = msg.get("lng")
                    if lat is not None and lng is not None:
                        state.weather_cache["lat"] = lat
                        state.weather_cache["lng"] = lng

                else:
                    await websocket.send_json({"type": "heartbeat", "status": "stable"})

            except Exception:
                await websocket.send_json({"type": "heartbeat", "status": "error"})

    except WebSocketDisconnect:
        state.manager.disconnect(websocket)
        print("[WebSocket] 切断されました。")

# ─────────────────────────────────────────────────────────────────────────
# アルバム（タイムライン）用エンドポイント
# ─────────────────────────────────────────────────────────────────────────

@app.api_route("/api/internal/list_timeline", methods=["GET", "POST"])
async def list_timeline_endpoint(
    limit: int = 40,
    include_reading: bool = False,
    include_movies: bool = False,
):
    """
    写真・動画・成長の節目を時系列で統合したタイムラインを返す（アルバムUI用）。
    例: /api/internal/list_timeline?limit=60&include_reading=true&include_movies=true
    """
    try:
        items = await timeline.get_timeline(
            limit=limit, include_reading=include_reading, include_movies=include_movies
        )
        return {"status": "ok", "count": len(items), "timeline": items}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ─────────────────────────────────────────────────────────────────────────
# 食事の記録（孤食ロボット機能）閲覧用エンドポイント
# ─────────────────────────────────────────────────────────────────────────
 

@app.api_route("/api/internal/list_meal_logs", methods=["GET", "POST"])
async def list_meal_logs_endpoint(limit: int = 30):
    """
    食事の記録を新しい順に返す（食事ページ専用。アルバムのタイムラインには含めない）。
    例: /api/internal/list_meal_logs?limit=30
    """
    try:
        logs = await get_recent_meal_logs(limit=limit)
        return {"status": "ok", "count": len(logs), "meal_logs": logs}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────
# 軽量リマインダー（タスク）一覧取得用エンドポイント
# ─────────────────────────────────────────────────────────────────────────

@app.api_route("/api/internal/list_reminders", methods=["GET", "POST"])
async def list_reminders_endpoint(limit: int = 20, include_done: bool = False):
    """
    登録済みのリマインダーを期限が近い順に返す（将来のUI用。会話Toolとは別経路）。
    例: /api/internal/list_reminders?limit=20&include_done=false
    """
    try:
        reminders = await get_upcoming_reminders(limit=limit, include_done=include_done)
        return {"status": "ok", "count": len(reminders), "reminders": reminders}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────
# 記憶ベース（ユーザー向けマイプロフィール）取得用エンドポイント
# ─────────────────────────────────────────────────────────────────────────

@app.api_route("/api/internal/get_memory_base", methods=["GET", "POST"])
async def get_memory_base_endpoint(meal_days: int = 30):
    """
    よく行く場所・最近の出来事・読書/映画の記録・食事の傾向・ルキルキから見た変化の一言・
    ユーザー自身の成長記録を横断集計して返す（memory_base_ui.html 専用。会話フローとは完全に別経路）。
    例: /api/internal/get_memory_base?meal_days=30
    """
    try:
        data = await build_memory_base(meal_days=meal_days)
        return {"status": "ok", "memory_base": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.api_route("/api/internal/list_growth_notes", methods=["GET", "POST"])
async def list_growth_notes_endpoint(limit: int = 20):
    """
    まがときさんが自分から語った成長・自慢の記録を新しい順に返す（将来のUI用。会話Toolとは別経路）。
    例: /api/internal/list_growth_notes?limit=20
    """
    try:
        notes = await get_recent_growth_notes(limit=limit)
        return {"status": "ok", "count": len(notes), "growth_notes": notes}
    except Exception as e:
        return {"status": "error", "message": str(e)}
 