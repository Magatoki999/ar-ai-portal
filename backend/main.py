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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
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
    check_nearby_spot,
    register_memory_spot,
    get_active_agent_memos,
    mark_memos_as_consumed,
    get_user_profile,
    save_username_to_db,
    save_user_profile_field,
)
from services.snap import generate_snap, upload_to_supabase_storage
from services.scheduler import auto_research_job, proactive_talk_job, trigger_proactive_speech, calendar_prep_job
from services.persona import (
    load_rukiruki_persona,
    load_magatoki_context,
    build_dynamic_constraints,
)

# agents モジュール（既存のまま）
from agents.router import analyze_and_route
from agents.graph import build_rukiruki_graph

load_dotenv()

# ─── LLM / ツール ───
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,
    openai_api_key=os.getenv("OPENAI_API_KEY"),
)
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
    scheduler.add_job(
        lambda: _run(proactive_talk_job, llm, MAGATOKI_KNOWLEDGE),
        "interval", minutes=1,
    )
    scheduler.add_job(
        lambda: _run(calendar_prep_job, llm),
        "cron", hour="8,12,15,18", minute=0,
        timezone=timezone(timedelta(hours=+9)),  # JST 8/12/15/18時に確実に実行する
    )
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


class MemoryPhotoRequest(BaseModel):
    arweave_tx_id: str = ""
    image_url: str


class SnapRequest(BaseModel):
    member_name: str
    camera_image: str
    wallet_address: str | None = None


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
            "calendar_context":          calendar_context,
            "growth_context":            growth_context,
            "episode_context":           episode_context,
            "emotion_context":           emotion_context,
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
            "_lat":                      lat,
            "_lng":                      lng,
        }
        result         = await rukiruki_graph.ainvoke(graph_input)
        ai_response    = result.get("ai_reply", ai_response)
        spatial_effect = result.get("spatial_effect", "cyber")
        active_memo_ids = result.get("active_memo_ids", active_memo_ids)
        arweave_tx_id  = result.get("arweave_tx_id", "")

        # ENGRAVE 判定
        _engrave = result.get("engrave_triggered", False)
        if not _engrave and any(k in payload.message for k in ["覚えて", "おぼえて", "記憶して"]):
            _engrave = True

        _show_image = result.get("show_image_url", "")
        print(
            f"[DEBUG] engrave_triggered={_engrave} "
            f"arweave_tx_id={bool(arweave_tx_id)} "
            f"show_image_url={bool(_show_image)}"
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
        # 場所名で episode_memories を検索し、見つかった image_url を show_image_url にセットする。
        loc_photo_match = re.search(r"\|\|SEARCH_LOCATION_PHOTO:(.*?)\|\|", ai_response)
        if loc_photo_match:
            loc_query = loc_photo_match.group(1).strip()
            ai_response = re.sub(r"\|\|SEARCH_LOCATION_PHOTO:.*?\|\|", "", ai_response).strip()
            if not _show_image:
                found_url = await find_episode_image_by_location(loc_query)
                if found_url:
                    _show_image = found_url

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
        _location = nearby_spot["name"] if nearby_spot else ""
        asyncio.create_task(
            maybe_save_episode(
                payload.message,
                ai_response,
                arweave_tx_id=result.get("arweave_tx_id", ""),
                location_name=_location,
                image_url=_photo_url,
                llm=llm,
            )
        )

        if active_memo_ids:
            await mark_memos_as_consumed(active_memo_ids)

        await state.manager.broadcast({"type": "status", "status": "talking", "text": ai_response})
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
        "engrave_triggered": result.get("engrave_triggered", False),
        "audio_mime":       audio_mime,
        "status":           "success",
    }


# ─────────────────────────────────────────────────────────────────────────────
# メモリ画像保存エンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/save_memory_image")
async def save_memory_image_endpoint(payload: MemoryImagePayload):
    ok = await update_episode_image_url(payload.image_url)
    return {"status": "ok" if ok else "error"}


@app.post("/api/memory/photo")
async def save_memory_photo(payload: MemoryPhotoRequest):
    ok = await update_episode_image_url(payload.image_url)
    if ok:
        return {"status": "ok", "image_url": payload.image_url}
    return {"status": "error"}


# ─────────────────────────────────────────────────────────────────────────────
# スナップエンドポイント
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/snap")
async def create_snap(payload: SnapRequest):
    image_url, error = await generate_snap(payload.member_name, payload.camera_image)
    if error:
        return {"status": "error", "message": error}

    # episode_memories に記録
    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%m月%d日 %H時%M分")
    await save_episode_memory(
        summary=f"{now_str}、まがときさんが{payload.member_name}とスナップ写真を撮影した。",
        mood_at_time=state.emotional_state.get("mood", "neutral"),
        keywords=["スナップ", payload.member_name, "写真", "思い出"],
        arweave_tx_id="",
        location_name="",
        image_url=image_url,
    )
    print("[スナップ] episode_memories に記録完了")

    return {
        "status":      "ok",
        "image_url":   image_url,
        "member_name": payload.member_name,
        "message":     f"{payload.member_name}とのスナップ写真ができたよ！",
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

                else:
                    await websocket.send_json({"type": "heartbeat", "status": "stable"})

            except Exception:
                await websocket.send_json({"type": "heartbeat", "status": "error"})

    except WebSocketDisconnect:
        state.manager.disconnect(websocket)
        print("[WebSocket] 切断されました。")
