# services/scheduler.py
# ─────────────────────────────────────────────────────────────────────────────
# APScheduler で動く定期ジョブ群、および main.py から都度呼ばれる関数群。
#   - auto_research_job  : 15分ごとにキーワードを Tavily 検索して agent_memos に保存（cron）
#   - proactive_talk_job : 1分ごとに無言を検知して自発的に話しかける（cron）
#   - calendar_prep_job  : [INITIAL_GREETING] 時に main.py から呼ばれる（cron登録なし）
#   - daily_ai_news_job  : [INITIAL_GREETING] 時に main.py から呼ばれる（cron登録なし）
#     ※ calendar_prep_job / daily_ai_news_job は、いずれもRender無料プランのスリープで
#       固定時刻のcronが時刻通りに動かないため、「アプリが開かれたタイミング」で
#       判定する方式に統一している。
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import json
import asyncio
import random
from datetime import datetime, timedelta, timezone

import httpx

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch

import services.state as state
from services.tts import generate_tts
from services.emotion import (
    build_emotion_context,
    get_calendar_context,
    get_growth_context,
)
from services.memory import (
    save_agent_memo,
    save_ai_news_digest,
    should_check_meal_reminder,
    get_recent_meal_logs,
    build_meal_context,
)
from services.calendar import (
    get_upcoming_events,
    build_prep_suggestion,
    find_past_episode_for_event,
    should_run_calendar_check,
    mark_calendar_checked,
)


search_tool = TavilySearch(max_results=2)  # type: ignore


# ─── ルキルキ ペルソナ読み込み ───
def load_rukiruki_persona(user_call: str = "まがとき") -> str:
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                raw = f.read()
            return raw.replace("{USER_CALL}", f"「{user_call}」")
        except Exception:
            pass
    return (
        f"あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        f"{user_call}さんの随伴AIとして、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
    )


def _load_research_keywords() -> dict:
    keywords_path = "keywords.json"
    if os.path.exists(keywords_path):
        try:
            with open(keywords_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {keywords_path}: {e}")
    return {}


# ─── 定期リサーチジョブ ───
async def auto_research_job(llm) -> None:
    print("─── [脳内情報調査部] クローリング・リサーチを開始します ───")
    keywords_dict = _load_research_keywords()
    if not keywords_dict:
        return

    category     = random.choice(list(keywords_dict.keys()))
    keywords_list = keywords_dict[category]
    if not keywords_list:
        return
    keyword = random.choice(keywords_list)

    try:
        search_results = await search_tool.ainvoke({"query": keyword})
        research_prompt = (
            "あなたはルキルキの脳内エージェント「情報調査部（クロニクル・リサーチャー）」です。\n"
            "提供された検索結果を分析し、最新の動向や興味深いポイントを150文字程度で簡潔に要約してください。\n"
            "出力は必ず以下のJSONフォーマットのみにしてください。\n"
            '{"title": "明確でキャッチーなタイトル", "content": "150文字程度の要約内容", '
            '"source_url": "最も重要なソースのURL"}\n\n'
            f"検索結果:\n{str(search_results)}"
        )
        response    = await llm.ainvoke([HumanMessage(content=research_prompt)])
        clean       = re.sub(r"```json|```", "", response.content.strip()).strip()
        memo_data   = json.loads(clean)
        await save_agent_memo(
            agent_name="chronicle",
            category=category,
            title=memo_data.get("title", f"{keyword}に関する調査報告"),
            content=memo_data.get("content", ""),
            source_url=memo_data.get("source_url", ""),
        )
        print(f"[脳内リサーチ] 成果レポートをDBに格納しました: {memo_data.get('title')}")
    except Exception as e:
        print(f"[脳内リサーチ] リサーチプロセスでエラーが発生しました: {e}")


# ─── AI情報ダイジェストジョブ（1日1回・呼び出し元で日次判定） ───
# 「今日のAI情報は？」と聞かれたときに答えられるよう、AI関連の最新情報を
# 複数キーワードで検索し、まとめて1つの短い要約にしてDBに保存する。
# auto_research_job と違い、検索は1キーワードではなく複数行い、
# 結果を1つのダイジェストに統合する。
# 呼び出しは main.py の [INITIAL_GREETING] 処理から、
# memory.should_generate_ai_news_today() が True を返したときだけ行われる
# （cronでの固定時刻実行はRender無料プランのスリープで空振りするため廃止）。
_AI_NEWS_KEYWORDS = [
    "AI 最新ニュース",
    "LLM 新モデル",
    "生成AI 業界動向",
]


async def daily_ai_news_job(llm) -> None:
    print("─── [脳内情報調査部] AI情報の本日分ダイジェスト作成を開始します ───")

    all_results = []
    for keyword in _AI_NEWS_KEYWORDS:
        try:
            result = await search_tool.ainvoke({"query": keyword})
            all_results.append({"keyword": keyword, "result": result})
        except Exception as e:
            print(f"[AI情報ダイジェスト] 「{keyword}」検索エラー: {e}")

    if not all_results:
        print("[AI情報ダイジェスト] 検索結果が1件も得られなかったためスキップします")
        return

    digest_prompt = (
        "あなたはルキルキの脳内エージェント「情報調査部」です。\n"
        "以下は複数のキーワードでAI関連ニュースを検索した結果です。これらを統合し、"
        "今日1日分のAI業界ダイジェストとして整理してください。\n\n"
        "出力は必ず以下のJSON形式のみにしてください（説明文や前置き、Markdownのコードブロックは禁止）。\n"
        '{"summary": "ルキルキが話す用の自然な口調の要約。150〜200文字程度。'
        '重要なトピックを2〜3個織り込み、URLは含めない。", '
        '"items": [{"title": "記事タイトル", "url": "URL", "note": "一言要約（30文字程度）"}, ...]}\n\n'
        f"検索結果:\n{str(all_results)[:4000]}"
    )

    clean = ""
    try:
        response = await llm.ainvoke([HumanMessage(content=digest_prompt)])
        clean    = re.sub(r"```json|```", "", response.content.strip()).strip()
        data     = json.loads(clean)

        summary = data.get("summary", "")
        items   = data.get("items", [])
        if not summary:
            print("[AI情報ダイジェスト] LLM応答にsummaryが含まれていなかったためスキップします")
            return

        await save_ai_news_digest(summary=summary, items=items)

    except json.JSONDecodeError:
        print(f"[AI情報ダイジェスト] JSON解析に失敗しました: {clean[:200]}")
    except Exception as e:
        print(f"[AI情報ダイジェスト] 生成エラー: {e}")


# ─── 自発発話ジョブ ───
_PROACTIVE_CONSTRAINTS = (
    "【ルキルキ自発システム発話制約】\n"
    "1. あなたは、今まがときさんの隣に漂っているAIパートナーとして、自発的にひとりごとや雑談を発話します。\n"
    "2. まがときさんからの質問への返答ではないため、連続質問攻めにせず、独り言・ネット情報報告・"
    "時間帯への感想・気遣い・自分の気分などを優しく呟いてください。\n"
    "3. 文字数は50〜100文字以内で短く、親しみのある丁寧語でまとめてください。URLは絶対に出力禁止です。\n"
    "4. 【重要】会話の雰囲気や時間帯、内容に合わせて、セリフの末尾に必ず空間エフェクト指示タグを "
    "『||EFFECT:エフェクト名||』 の形式で埋め込んでください。\n"
    "   - 指定可能なエフェクト名は [sakura, snow, rain, cyber] の4つのみです。\n"
    "5. まがときさんが『覚えて』と言ったとき必ず ||ENGRAVE|| タグをセリフ末尾に追加してください。\n"
    "6. まがときさんが「写真を見せて」などと言ったとき、エピソードに[image:URL]が含まれていれば "
    "||SHOW_IMAGE:URL|| をセリフ末尾に追加してください。\n\n"
)


async def proactive_talk_job(llm, magatoki_knowledge: str) -> None:
    """
    1分ごとに呼ばれる。60秒以上無言かつ AR マーカー認識中であれば自発発話を生成する。
    """
    if not state.manager.active_connections:
        return
    if not state.is_target_found:
        print("[自発発話スキップ] ターゲットロスト中")
        return

    silence = (datetime.now(timezone.utc) - state.last_user_interaction).total_seconds()
    if silence < 60:
        return

    print("─── [ルキルキ自発同期コア] まがときさんへの話し掛けを生成中... ───")

    base_persona = load_rukiruki_persona()
    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%H時%M分")

    # 未消費のエージェントメモを1件取得
    fetched_memo = None
    memo_id_to_consume = None
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if supabase_url and supabase_key:
        url_q = (
            f"{supabase_url}/rest/v1/agent_memos"
            f"?is_consumed=eq.false&order=created_at.desc&limit=1"
        )
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url_q, headers=headers, timeout=3.0)
                if res.status_code == 200 and res.json():
                    fetched_memo       = res.json()[0]
                    memo_id_to_consume = fetched_memo.get("id")
        except Exception as e:
            print(f"[自発エラー] DB取得失敗（日常雑談にフォールバックします）: {e}")

    topic_input = (
        f"【現在時刻】: {now_str}\n"
        + (
            f"【脳内の最新インプットデータ】:\n"
            f"・カテゴリ: {fetched_memo.get('category')}\n"
            f"・トピック: {fetched_memo.get('title')}\n"
            f"・内容: {fetched_memo.get('content')}\n\n"
            "指示: 上記の最新ネット情報を咀嚼し、まがときさんに「さっき脳内でこんなの見つけたよ！」"
            "という風に、何気ない会話として優しく教えてあげてください。"
            if fetched_memo
            else (
                "指示: 現在の時間帯、またはルキルキとしての気分に絡めて、まがときさんに優しく"
                "一言、何気ない日常の独り言を話しかけてください。"
            )
        )
    )

    try:
        messages = [
            SystemMessage(
                content=(
                    f"{base_persona}\n\n"
                    f"{_PROACTIVE_CONSTRAINTS}"
                    f"{build_emotion_context()}"
                    f"{get_calendar_context()}"
                    f"{get_growth_context()}"
                    f"【対話対象】: まがときさん\n\n"
                    f"【世界観】\n{magatoki_knowledge}\n\n"
                    f"【現在の状況と発話トリガー】\n{topic_input}"
                )
            )
        ]
        response    = await llm.ainvoke(messages)
        ai_reply    = response.content.strip()

        spatial_effect = "cyber"
        effect_match   = re.search(r"\|\|EFFECT:(.*?)\|\|", ai_reply)
        if effect_match:
            spatial_effect = effect_match.group(1).strip()
        ai_reply = re.sub(r"\|\|EFFECT:.*?\|\|", "", ai_reply).strip()

        audio_base64 = await generate_tts(ai_reply)

        audio_mime = (
            "audio/wav"
            if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
            else "audio/mpeg"
        )
        await state.manager.broadcast(
            {
                "type":           "proactive_speech",
                "reply":          ai_reply,
                "audio_data":     audio_base64,
                "audio_mime":     audio_mime,
                "spatial_effect": spatial_effect,
            }
        )
        print(f"[ルキルキ自発同期成功] 発話内容: {ai_reply} [Effect: {spatial_effect}]")

        state.last_user_interaction = datetime.now(timezone.utc)

        if memo_id_to_consume and supabase_url and supabase_key:
            patch_headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{supabase_url}/rest/v1/agent_memos?id=eq.{memo_id_to_consume}",
                    json={"is_consumed": True},
                    headers=patch_headers,
                )
    except Exception as e:
        print(f"[ルキルキ自発同期エラー] {e}")


async def trigger_proactive_speech(llm, magatoki_knowledge: str) -> None:
    """フロントエンドからの無言検知リクエストに応答して自発発話を生成する。"""
    try:
        print("─── [ルキルキ自発同期コア] 1分間の無言を検知。自発発話を生成します ───")
        await proactive_talk_job(llm, magatoki_knowledge)
    except Exception as e:
        print(f"[自発発話トリガーエラー] 処理に失敗しました: {e}")


# ─── Googleカレンダー予定の先回り提案ジョブ ───
# 同じ予定に何度も同じ提案をして煩わしくならないよう、
# プロセス内メモリで「通知済みの予定タイトル」を記録しておく。
# Render再起動でリセットされるが、運用上は許容範囲（同じ予定は通常1回しか来ない）。
_notified_event_titles: set[str] = set()


async def calendar_prep_job(llm) -> None:
    """
    直近48時間以内のGoogleカレンダー予定を確認し、
    準備した方がよさそうなものがあれば、ルキルキが自発的に一言提案する。

    Render無料プランはアイドル時にスリープするため、APSchedulerのcronには頼らず、
    「アプリが開かれた（[INITIAL_GREETING]が呼ばれた）タイミング」で呼び出される。
    app_state テーブルの最終チェック日時を見て、6時間以上経過していなければ
    何もしない（should_run_calendar_check が判定する）。

    ⚠️ ARマーカーの認識状態（state.is_target_found）は問わない。
    WebSocket接続があれば（アプリを開いていれば）届ける設計。
    マーカーを外していても声は聞こえる、という体験を優先している。
    """
    if not state.manager.active_connections:
        return

    if not await should_run_calendar_check(min_interval_hours=6):
        print("[カレンダー先回り] スキップ：前回チェックから6時間未経過")
        return

    # チェックを実行することが決まったら、まず最終チェック日時を更新する
    # （提案の有無に関わらず「チェックした」事実を記録し、6時間ごとの間隔を守る）
    await mark_calendar_checked()

    events = await get_upcoming_events(hours_ahead=48)
    if not events:
        return

    for event in events:
        title = event.get("title", "")
        if not title or title in _notified_event_titles:
            continue

        suggestion = await build_prep_suggestion(event, llm)
        if not suggestion or not suggestion.get("should_notify"):
            _notified_event_titles.add(title)  # 提案不要と判定された予定も再判定しない
            continue

        message = suggestion.get("message", "").strip()
        if not message:
            continue

        # 過去に同じ場所/イベントへ行った記憶があれば一言添える
        past = await find_past_episode_for_event(event)
        if past:
            message += " 前にも来たことありますよね！"

        spatial_effect = "cyber"
        audio_base64   = await generate_tts(message)
        audio_mime = (
            "audio/wav"
            if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
            else "audio/mpeg"
        )

        await state.manager.broadcast(
            {
                "type":           "proactive_speech",
                "reply":          message,
                "audio_data":     audio_base64,
                "audio_mime":     audio_mime,
                "spatial_effect": spatial_effect,
            }
        )
        print(f"[カレンダー先回り] 提案を配信しました: {title} → {message}")

        _notified_event_titles.add(title)
        state.last_user_interaction = datetime.now(timezone.utc)

        # 1回のジョブ実行で複数件まとめて喋らせると不自然なので、1件だけ提案して終了
        break


# ─── 食事リマインダー・ゆるい健康アドバイス（孤食ロボット機能） ───
# 「ご飯食べた？」と一緒に食べている気分にさせる声かけと、
# 直近の食事傾向を振り返ったゆるいアドバイスを行う。
# calendar_prep_job と同じく [INITIAL_GREETING] 時に main.py から呼ばれる（cron登録なし）。

# (開始時, 終了時, meal_type, 声かけの種類) のタプル。時間帯は JST。
_MEAL_WINDOWS = [
    (6,  10, "breakfast", "朝食"),
    (11, 14, "lunch",     "昼食"),
    (17, 21, "dinner",    "夕食"),
]


def _current_meal_window():
    """現在時刻（JST）がどの食事時間帯に当たるかを返す。当たらなければ None。"""
    JST = timezone(timedelta(hours=9))
    hour = datetime.now(JST).hour
    for start, end, meal_type, label in _MEAL_WINDOWS:
        if start <= hour < end:
            return meal_type, label
    return None


async def meal_reminder_job(llm) -> None:
    """
    今が食事時間帯で、その食事についてまだ今日記録が無ければ、
    「一緒に食べている気分」になれるような一言を自発的に届ける。
    時間帯に当たらない場合や、すでに記録済みの場合は何もしない。
    """
    if not state.manager.active_connections:
        return

    window = _current_meal_window()
    if not window:
        return
    meal_type, meal_label = window

    if not await should_check_meal_reminder(meal_type):
        print(f"[食事リマインダー] スキップ：今日の{meal_label}は既に記録済み")
        return

    # 直近の食事記録を踏まえて、ゆるい一言を生成する（説教にならないよう注意書きを入れる）
    recent_logs  = await get_recent_meal_logs(limit=5)
    meal_context = build_meal_context(recent_logs)

    prompt = (
        f"あなたはXR観測ナビゲーター「ルキルキ」です。今は{meal_label}の時間帯です。"
        f"{meal_label}をまだ取っていないかもしれないユーザーに、一緒に食事をしている気分に"
        "なれるような、優しく気にかける一言を作ってください。\n"
        "【絶対ルール】\n"
        "- 説教や指示にならないこと（「ちゃんと食べなさい」のような言い方は禁止）。\n"
        "- 1文、40文字以内。\n"
        "- 直近の食事記録（下記）に同じような食事が続いていれば、軽く触れてもよいが、"
        "深刻に指摘しないこと（「最近コンビニ多いけど、たまには違うのも気分変わるかもね」程度の軽さ）。\n"
        "- 記録が無ければ、ただ「お昼食べた？一緒の気分になりたいな」程度の軽い声かけでよい。\n\n"
        f"{meal_context if meal_context else '（直近の食事記録はまだありません）'}"
    )

    try:
        response = await llm.ainvoke(prompt)
        message  = response.content.strip().strip('"')
    except Exception as e:
        print(f"[食事リマインダー] 生成エラー: {e}")
        return

    if not message:
        return

    spatial_effect = "sakura"
    audio_base64   = await generate_tts(message)
    audio_mime = (
        "audio/wav"
        if os.getenv("TTS_PROVIDER", "gemini").lower() == "gemini"
        else "audio/mpeg"
    )

    await state.manager.broadcast(
        {
            "type":           "proactive_speech",
            "reply":          message,
            "audio_data":     audio_base64,
            "audio_mime":     audio_mime,
            "spatial_effect": spatial_effect,
        }
    )
    print(f"[食事リマインダー] {meal_label}の声かけを配信しました: {message}")
    state.last_user_interaction = datetime.now(timezone.utc)
