# services/memory.py
# ─────────────────────────────────────────────────────────────────────────────
# Supabase との全 DB 操作を担当するサービスモジュール。
#   - エピソードメモリ（episode_memories）
#   - メモリースポット（memory_spots）
#   - ユーザープロフィール（user_profiles）
#   - エージェントメモ（agent_memos）
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import json
import math
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage

from services.state import emotional_state


# ─── Supabase 接続情報（モジュールレベルで解決） ───
def _sb() -> tuple[str | None, str | None]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    return url, key


def _sb_headers() -> dict:
    _, key = _sb()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


# ═══════════════════════════════════════════════════════
# エピソードメモリ
# ═══════════════════════════════════════════════════════

async def save_episode_memory(
    summary: str,
    mood_at_time: str,
    keywords: list,
    arweave_tx_id: str = "",
    location_name: str = "",
    image_url: str = "",
    lat: float | None = None,
    lng: float | None = None,
) -> None:
    url, key = _sb()
    if not url or not key:
        return
    endpoint = f"{url}/rest/v1/episode_memories"
    headers = {**_sb_headers(), "Content-Type": "application/json"}
    data: dict = {
        "summary": summary,
        "mood_at_time": mood_at_time,
        "keywords": keywords,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if arweave_tx_id:
        data["arweave_tx_id"] = arweave_tx_id
    if location_name:
        data["location_name"] = location_name
    if image_url:
        data["image_url"] = image_url
    # lat/lng は 0.0 のような正当な値もあり得るため、None チェックで判定する
    # （location_name 等の "truthy" チェックとは意図的に分けている）
    if lat is not None and lng is not None:
        data["lat"] = lat
        data["lng"] = lng
    try:
        async with httpx.AsyncClient() as client:
            await client.post(endpoint, json=data, headers=headers, timeout=5.0)
    except Exception as e:
        print(f"[エピソード保存エラー] {e}")


async def get_recent_episodes(limit: int = 8) -> str:
    """
    エピソードメモリを取得し、時間軸グループ（今日/昨日/今週/それ以前）と
    節目（ちょうど1週間前・1ヶ月前）付きでプロンプト文字列として返す。
    """
    url, key = _sb()
    if not url or not key:
        return ""
    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?order=created_at.desc&limit={limit}&select=*"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code != 200 or not res.json():
                return ""

            episodes = res.json()
            JST   = timezone(timedelta(hours=+9))
            today = datetime.now(JST).date()
            groups: dict[str, list[str]] = {
                "today": [], "yesterday": [], "this_week": [], "older": []
            }
            milestones: list[str] = []

            for ep in episodes:
                raw = ep.get("created_at", "")
                if not raw:
                    continue
                try:
                    ep_dt    = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(JST)
                    ep_date  = ep_dt.date()
                    diff_days = (today - ep_date).days
                    time_str  = ep_dt.strftime("%m月%d日 %H時%M分")
                    image_note = " 📷写真あり" if ep.get("image_url") else ""
                    image_tag  = f" [image:{ep['image_url']}]" if ep.get("image_url") else ""
                    entry = (
                        f"・{time_str} ─ {ep.get('summary','')}（気分: {ep.get('mood_at_time','')}）"
                        f"{image_note}{image_tag}"
                    )
                    if diff_days == 7:
                        milestones.append(f"📅 ちょうど1週間前（{time_str}）─ {ep.get('summary','')}")
                    elif diff_days == 30:
                        milestones.append(f"📅 ちょうど1ヶ月前（{time_str}）─ {ep.get('summary','')}")

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

            _t = len(groups["today"]); _y = len(groups["yesterday"])
            _w = len(groups["this_week"]); _o = len(groups["older"])
            print(f"[エピソード取得] today={_t} yesterday={_y} week={_w} older={_o} milestones={len(milestones)}")
            for ep in episodes:
                if ep.get("image_url"):
                    print(f"[エピソード取得] 📷写真あり: {ep.get('created_at','')[:16]} image_url={ep['image_url'][:60]}")

            if not any(groups.values()) and not milestones:
                return ""

            lines = ["【ルキルキの記憶 / 時間軸エピソード】"]
            if milestones:
                lines.append("【節目の記憶】")
                lines.extend(milestones)
                lines.append("")
            for label, key_name in [
                ("今日", "today"), ("昨日", "yesterday"),
                ("今週", "this_week"), ("それ以前", "older"),
            ]:
                if groups[key_name]:
                    lines.append(f"【{label}の記憶】")
                    lines.extend(groups[key_name])
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


async def maybe_save_episode(
    user_text: str,
    ai_reply: str,
    arweave_tx_id: str = "",
    location_name: str = "",
    image_url: str = "",
    llm=None,
    lat: float | None = None,
    lng: float | None = None,
) -> None:
    """
    記憶キーワードにマッチした会話か、ENGRAVE トリガーが発火した場合にのみ保存する。
    llm を渡すとキーワードを LLM 抽出する（省略時はルールベース）。
    lat/lng を渡すと、その時点のGPS座標も一緒に記録する
    （「この場所の写真見せて」のような指示語ベースの検索に使うため）。
    """
    memorable_keywords = [
        "完成", "できた", "やった", "疲れた", "眠い", "バグ", "お香",
        "神社", "京都", "Blender", "ArtAR", "ありがとう", "ルキルキ",
        "覚えて", "おぼえて", "記憶して",
    ]
    force_save = (
        arweave_tx_id != ""
        or image_url != ""  # SAVE_PHOTO で写真が撮れた場合は確実に保存する
        or any(k in user_text for k in ["覚えて", "おぼえて", "記憶して"])
    )
    if not force_save and not any(k in user_text for k in memorable_keywords):
        return

    JST     = timezone(timedelta(hours=+9))
    now_str = datetime.now(JST).strftime("%m月%d日 %H時%M分")
    summary = (
        f"{now_str}、まがときさんが「{user_text[:40]}」と言った。"
        f"ルキルキは「{ai_reply[:40]}」と答えた。"
    )

    # キーワード抽出（LLM 優先、フォールバックはルールベース）
    keywords: list[str]
    if llm:
        try:
            kw_prompt = (
                "以下の会話から重要なキーワードを3〜5個抽出して、JSONの文字列配列のみで返してください。\n"
                "説明や前置きは不要です。例: [\"京都\", \"ArtAR\", \"バグ修正\"]\n\n"
                f"ユーザー: {user_text}\nルキルキ: {ai_reply}"
            )
            kw_res = await llm.ainvoke([HumanMessage(content=kw_prompt)])
            kw_text = re.sub(r"```json|```", "", kw_res.content.strip()).strip()
            extracted = json.loads(kw_text)
            if not isinstance(extracted, list):
                raise ValueError("list expected")
            keywords = [str(k) for k in extracted[:5]]
        except Exception as kw_err:
            keywords = [k for k in memorable_keywords if k in user_text]
            print(f"[キーワード抽出] LLM失敗→フォールバック: {kw_err}")
    else:
        keywords = [k for k in memorable_keywords if k in user_text]

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
        image_url=image_url,
        lat=lat,
        lng=lng,
    )


async def update_episode_image_url(image_url: str) -> bool:
    """直近のエピソードメモリに image_url を紐づける。"""
    url, key = _sb()
    if not url or not key:
        return False
    headers = _sb_headers()
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{url}/rest/v1/episode_memories?order=created_at.desc&limit=1",
                headers=headers,
                timeout=5.0,
            )
            if res.status_code == 200 and res.json():
                record_id = res.json()[0]["id"]
                patch_headers = {**headers, "Content-Type": "application/json"}
                await client.patch(
                    f"{url}/rest/v1/episode_memories?id=eq.{record_id}",
                    json={"image_url": image_url},
                    headers=patch_headers,
                    timeout=5.0,
                )
                print(f"[思い出写真] 保存完了: {image_url}")
                return True
    except Exception as e:
        print(f"[思い出写真] 保存エラー: {e}")
    return False


async def find_episode_image_by_location(location_name: str) -> str | None:
    """
    指定した場所名（location_name）に紐づく、image_url が入っている
    エピソードメモリの中から最も新しいものの image_url を返す。
    見つからなければ None。
    部分一致（ilike）で検索し、表記揺れに多少強くする。
    """
    url, key = _sb()
    if not url or not key or not location_name:
        return None

    # PostgREST の ilike は * をワイルドカードとして使う
    pattern = f"*{location_name.strip()}*"
    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?location_name=ilike.{pattern}"
        f"&image_url=not.is.null"
        f"&order=created_at.desc&limit=1&select=image_url,location_name,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code == 200:
                rows = res.json()
                if rows and rows[0].get("image_url"):
                    print(
                        f"[場所検索] 「{location_name}」一致: "
                        f"{rows[0].get('location_name')} "
                        f"({rows[0].get('created_at','')[:16]})"
                    )
                    return rows[0]["image_url"]
            else:
                print(f"[場所検索エラー] status={res.status_code} body={res.text[:200]}")
    except Exception as e:
        print(f"[場所検索エラー] {e}")

    print(f"[場所検索] 「{location_name}」に該当する写真なし")
    return None


# ═══════════════════════════════════════════════════════
# メモリースポット
# ═══════════════════════════════════════════════════════

MEMORY_SPOTS_TABLE = "memory_spots"


async def get_memory_spots() -> list:
    url, key = _sb()
    if not url or not key:
        return []
    endpoint = f"{url}/rest/v1/{MEMORY_SPOTS_TABLE}?order=created_at.desc"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        print(f"[メモリースポット取得エラー] {e}")
    return []


async def register_memory_spot(
    name: str, lat: float, lng: float,
    name_reading: str = "", radius_m: int = 100
) -> bool:
    url, key = _sb()
    if not url or not key:
        return False
    endpoint = f"{url}/rest/v1/{MEMORY_SPOTS_TABLE}"
    headers = {**_sb_headers(), "Content-Type": "application/json"}
    data = {
        "name":         name,
        "name_reading": name_reading or name,  # 読み未指定なら表示名をそのまま使う
        "lat":          lat,
        "lng":          lng,
        "radius_m":     radius_m,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
            return res.status_code in (200, 201)
    except Exception as e:
        print(f"[メモリースポット登録エラー] {e}")
        return False


def calc_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 式で2点間の距離をメートルで返す。"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def check_nearby_spot(lat: float, lng: float) -> dict | None:
    spots = await get_memory_spots()
    for spot in spots:
        dist = calc_distance_m(lat, lng, spot["lat"], spot["lng"])
        if dist <= spot.get("radius_m", 100):
            return spot
    return None


async def find_episode_image_by_proximity(
    lat: float, lng: float, radius_m: float = 150
) -> str | None:
    """
    「この場所の写真見せて」「ここの写真ある？」のような、固有の場所名を
    含まない指示語ベースの依頼に対応するための検索。
    find_episode_image_by_location() が location_name の文字列一致で探すのに対し、
    こちらは現在のGPS座標から radius_m メートル以内に記録された、
    image_url 付きのエピソードメモリの中から最も新しいものを返す。

    注意: lat/lng 列を持たない古い記憶（このカラム追加より前に保存されたもの）は
    対象にならない。今後保存される記憶からのみ検索対象になる。
    """
    url, key = _sb()
    if not url or not key:
        return None

    # まずPostgREST側で大まかな範囲（緯度経度の±矩形）に絞り込み、
    # 件数を抑えてからPython側でHaversineの正確な距離判定を行う。
    # 緯度1度 ≈ 111km なので、radius_mを度数に変換する簡易換算。
    deg_margin = (radius_m / 111_000) * 1.5  # 矩形は円より広いので少し余裕を持たせる
    lat_min, lat_max = lat - deg_margin, lat + deg_margin
    lng_min, lng_max = lng - deg_margin, lng + deg_margin

    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?lat=gte.{lat_min}&lat=lte.{lat_max}"
        f"&lng=gte.{lng_min}&lng=lte.{lng_max}"
        f"&image_url=not.is.null"
        f"&order=created_at.desc&limit=20"
        f"&select=image_url,location_name,lat,lng,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[近接検索エラー] status={res.status_code} body={res.text[:200]}")
            return None

        rows = res.json()
        if not rows:
            print(f"[近接検索] 半径{radius_m}m以内に該当する写真なし")
            return None

        # 矩形で絞り込んだ候補の中から、実際にHaversine距離が radius_m 以内かつ
        # 最も新しいもの（rowsはcreated_at降順なので先頭から探索）を選ぶ
        for row in rows:
            row_lat, row_lng = row.get("lat"), row.get("lng")
            if row_lat is None or row_lng is None:
                continue
            dist = calc_distance_m(lat, lng, row_lat, row_lng)
            if dist <= radius_m:
                print(
                    f"[近接検索] 一致: {row.get('location_name', '（名称なし）')} "
                    f"距離={dist:.0f}m ({row.get('created_at', '')[:16]})"
                )
                return row["image_url"]

        print(f"[近接検索] 矩形内に{len(rows)}件あったが、半径{radius_m}m以内に該当なし")
        return None

    except Exception as e:
        print(f"[近接検索エラー] {e}")
        return None


# ═══════════════════════════════════════════════════════
# ユーザープロフィール
# ═══════════════════════════════════════════════════════

async def get_user_profile(wallet_address: str) -> dict | None:
    url, key = _sb()
    if not url or not key or not wallet_address:
        return None
    endpoint = (
        f"{url}/rest/v1/user_profiles"
        f"?wallet_address=eq.{wallet_address.lower()}"
        f"&select=user_name,preferred_call,birthday"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                if data:
                    return data[0]
    except Exception as e:
        print(f"[プロフィール取得エラー] {e}")
    return None


async def save_user_profile_field(
    wallet_address: str, field: str, value: str
) -> None:
    url, key = _sb()
    if not url or not key or not wallet_address:
        return
    headers = {
        **_sb_headers(),
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    data = {"wallet_address": wallet_address.lower(), field: value}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{url}/rest/v1/user_profiles",
                json=data,
                headers=headers,
                timeout=5.0,
            )
            print(f"[プロフィール更新] {field} = {value}")
    except Exception as e:
        print(f"[プロフィール更新エラー] {e}")


async def save_username_to_db(wallet_address: str, name: str) -> None:
    """後方互換 API。save_user_profile_field の薄いラッパー。"""
    await save_user_profile_field(wallet_address, "user_name", name)


# ═══════════════════════════════════════════════════════
# エージェントメモ
# ═══════════════════════════════════════════════════════

async def save_agent_memo(
    agent_name: str, category: str, title: str, content: str, source_url: str
) -> None:
    url, key = _sb()
    if not url or not key:
        return
    headers = {**_sb_headers(), "Content-Type": "application/json"}
    data = {
        "agent_name": agent_name,
        "category":   category,
        "title":      title,
        "content":    content,
        "importance": 3,
        "metadata":   {"source_url": source_url},
        "is_consumed": False,
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{url}/rest/v1/agent_memos", json=data, headers=headers, timeout=5.0
            )
            if res.status_code not in (200, 201):
                print(
                    f"[脳内エラー] Supabaseへの報告書保存に失敗しました: "
                    f"{res.status_code} {res.text}"
                )
    except Exception as e:
        print(f"[脳内エラー] 保存処理中に例外が発生しました: {e}")


async def get_active_agent_memos(selected_agents: list) -> tuple[str, list]:
    url, key = _sb()
    if not url or not key or not selected_agents:
        return "", []

    agents_str = ",".join(selected_agents)
    endpoint = (
        f"{url}/rest/v1/agent_memos"
        f"?agent_name=in.({agents_str})&is_consumed=eq.false"
        f"&order=created_at.desc&limit=3"
    )
    combined_memos = ""
    memo_ids: list = []
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code == 200:
                memos = res.json()
                for memo in memos:
                    meta    = memo.get("metadata") or {}
                    url_str = (
                        meta.get("source_url", "")
                        if isinstance(meta, dict)
                        else memo.get("source_url", "")
                    )
                    combined_memos += (
                        f"\n【裏側エージェント共有知識 ({memo.get('agent_name')})】\n"
                        f"・トピック: {memo.get('category')} / {memo.get('title', '')}\n"
                        f"・思考内容: {memo.get('content')}\n"
                    )
                    if url_str:
                        combined_memos += f"・ソースURL: {url_str}\n"
                    if "id" in memo:
                        memo_ids.append(memo["id"])
    except Exception as e:
        print(f"Error fetching active agent memos: {e}")

    return combined_memos, memo_ids


async def mark_memos_as_consumed(memo_ids: list) -> None:
    url, key = _sb()
    if not url or not key or not memo_ids:
        return
    headers = {**_sb_headers(), "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        for memo_id in memo_ids:
            try:
                await client.patch(
                    f"{url}/rest/v1/agent_memos?id=eq.{memo_id}",
                    json={"is_consumed": True},
                    headers=headers,
                    timeout=5.0,
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════
# AI情報ダイジェスト（毎日1回、AI関連の最新情報をまとめたもの）
# 「今日のAI情報は？」と聞かれたときに参照する。
# scheduler.py の daily_ai_news_job() から1日1回保存され、
# agents/nodes.py の get_today_ai_news Tool から読み出される。
# ═══════════════════════════════════════════════════════

async def save_ai_news_digest(
    summary: str,
    items: list | None = None,
    digest_date: str | None = None,
) -> bool:
    """
    その日のAI情報ダイジェストを保存する。
    digest_date は "YYYY-MM-DD" 形式の文字列。省略時は今日の日付（JST）を使う。
    digest_date は UNIQUE 制約があるため、同じ日に複数回呼ばれた場合は
    upsert（Prefer: resolution=merge-duplicates）で上書きする。
    """
    url, key = _sb()
    if not url or not key:
        return False

    if digest_date is None:
        digest_date = datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=9))
        ).strftime("%Y-%m-%d")

    endpoint = f"{url}/rest/v1/ai_news_digest"
    headers = {
        **_sb_headers(),
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    data = {
        "digest_date": digest_date,
        "summary": summary,
        "items": items or [],
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=10.0)
        if res.status_code not in (200, 201):
            print(f"[AI情報ダイジェスト保存エラー] status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[AI情報ダイジェスト] {digest_date} 分を保存しました")
        return True
    except Exception as e:
        print(f"[AI情報ダイジェスト保存エラー] {e}")
        return False


async def get_latest_ai_news_digest() -> dict | None:
    """
    最新のAI情報ダイジェストを1件取得する。
    「今日のAI情報は？」と聞かれたとき、まずこれを呼ぶ。
    その日の分がまだ無い場合（ジョブ実行前など）は、直前に保存された分が返る
    （呼び出し側で digest_date を見て「今日」か「以前」かを判断できる）。
    見つからない場合は None。
    """
    url, key = _sb()
    if not url or not key:
        return None

    endpoint = (
        f"{url}/rest/v1/ai_news_digest"
        f"?order=digest_date.desc&limit=1"
        f"&select=digest_date,summary,items,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[AI情報ダイジェスト取得エラー] status={res.status_code} body={res.text[:200]}")
            return None
        rows = res.json()
        if not rows:
            print("[AI情報ダイジェスト] 保存済みのダイジェストがありません")
            return None
        return rows[0]
    except Exception as e:
        print(f"[AI情報ダイジェスト取得エラー] {e}")
        return None


@tool
async def get_today_ai_news() -> str:
    """
    ユーザーから「今日のAI情報は？」「最近のAIニュースある？」のように
    AI業界の最新情報について聞かれたときに呼ぶツール。
    daily_ai_news_job が毎日1回保存したダイジェストの中から最新のものを返す。
    まだ1件も保存されていない場合は、その旨を伝える文字列を返す。
    """
    print("[AI情報ダイジェスト] get_today_ai_news が呼ばれました")
    digest = await get_latest_ai_news_digest()
    if not digest:
        print("[AI情報ダイジェスト] 該当データなし。未生成メッセージを返します")
        return "AI情報のダイジェストはまだ用意できていません。"

    digest_date = digest.get("digest_date", "")
    summary     = digest.get("summary", "")

    # JST基準で「今日」かどうかを判定し、古い情報なら一言添える
    today_str = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d")

    if digest_date == today_str:
        print(f"[AI情報ダイジェスト] {digest_date}分（本日分）を返します")
        return summary
    else:
        print(f"[AI情報ダイジェスト] {digest_date}分（本日分ではない）を返します")
        return f"（{digest_date}時点の情報です）{summary}"

