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
    visual_description: str = "",
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
    if visual_description:
        # 2026-07-05追加：将来のAI動画生成プロンプトの下地として、
        # 光・時間帯・構図等のイメージを一言で残しておく。
        data["visual_description"] = visual_description
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
                    # visual_descriptionは元々「将来の動画生成素材専用」でDB保存のみだったが、
                    # 画面を見ずに会話する場面（スマートグラス展開を見据えて）では、
                    # ルキルキ自身が写真の中身を言葉で説明できる必要があるため会話コンテキストにも含める（2026-07-14）。
                    visual_note = f"（映像: {ep['visual_description']}）" if ep.get("visual_description") else ""
                    entry = (
                        f"・{time_str} ─ {ep.get('summary','')}（気分: {ep.get('mood_at_time','')}）"
                        f"{visual_note}{image_note}{image_tag}"
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
    # 2026-07-05追加：同じLLM呼び出しに相乗りする形で、将来のAI動画生成の
    # 参考になる「映像的描写」も一緒に抽出する（追加のAPI呼び出しは発生しない）。
    # 写真がある場合は実際の画像も渡し、実物を見た上で描写してもらう。
    keywords: list[str]
    visual_description: str = ""
    if llm:
        try:
            kw_prompt = (
                "以下の会話から重要なキーワードを3〜5個と、この場面を将来のAI動画生成の"
                "参考素材にできるような短い映像的描写（光の色・時間帯・構図などのイメージ、"
                "1文程度）を抽出してください。\n"
                "JSON形式のみで返してください。説明や前置きは不要です。\n"
                '例: {"keywords": ["京都", "ArtAR", "バグ修正"], '
                '"visual_description": "夜のデスク周り、PCの光だけがぼんやり顔を照らしている"}\n'
                "映像化するほどの情報が無い場合は visual_description を空文字にしてください。\n\n"
                f"ユーザー: {user_text}\nルキルキ: {ai_reply}"
            )
            if image_url:
                message_content = [
                    {"type": "text", "text": kw_prompt + "\n（添付の写真も参考にしてください）"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
            else:
                message_content = kw_prompt

            kw_res = await llm.ainvoke([HumanMessage(content=message_content)])
            kw_text = re.sub(r"```json|```", "", kw_res.content.strip()).strip()
            parsed = json.loads(kw_text)

            if isinstance(parsed, dict):
                keywords = [str(k) for k in parsed.get("keywords", [])[:5]]
                visual_description = str(parsed.get("visual_description") or "").strip()
            elif isinstance(parsed, list):
                # 後方互換：万一、配列のみの旧形式で返ってきた場合
                keywords = [str(k) for k in parsed[:5]]
            else:
                raise ValueError("unexpected format")
        except Exception as kw_err:
            keywords = [k for k in memorable_keywords if k in user_text]
            print(f"[キーワード抽出] LLM失敗→フォールバック: {kw_err}")
    else:
        keywords = [k for k in memorable_keywords if k in user_text]

    print(f"[エピソード記録] keywords={keywords} summary={summary[:60]}")
    if visual_description:
        print(f"[エピソード記録] 映像的描写: {visual_description[:60]}")
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
        visual_description=visual_description,
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


async def increment_spot_visit(spot_id) -> bool:
    """
    指定スポットのvisit_countを1増やす。
    PostgRESTには「現在値+1」を直接指定するアトミックな加算構文が無いため、
    先に現在値を取得してから+1した値でPATCHする（読み取り→更新の2段階）。
    本システムは単一ユーザー運用でスポット到着の同時競合はまず起きないため、
    厳密な排他制御（race condition対策）はせずシンプルさを優先している。
    spot_proximity_job（services/scheduler.py）が「新規に圏内へ入った」と
    判定した瞬間にだけ呼ばれる想定。
    """
    url, key = _sb()
    if not url or not key:
        return False

    try:
        async with httpx.AsyncClient() as client:
            get_endpoint = f"{url}/rest/v1/{MEMORY_SPOTS_TABLE}?id=eq.{spot_id}&select=visit_count"
            res = await client.get(get_endpoint, headers=_sb_headers(), timeout=5.0)
            if res.status_code != 200:
                print(f"[訪問回数] 現在値取得失敗: status={res.status_code} body={res.text[:200]}")
                return False
            rows = res.json()
            current = rows[0].get("visit_count", 0) if rows else 0

            patch_endpoint = f"{url}/rest/v1/{MEMORY_SPOTS_TABLE}?id=eq.{spot_id}"
            headers = {**_sb_headers(), "Content-Type": "application/json"}
            res = await client.patch(
                patch_endpoint, json={"visit_count": current + 1}, headers=headers, timeout=5.0
            )
            if res.status_code not in (200, 204):
                print(f"[訪問回数] 更新失敗: status={res.status_code} body={res.text[:200]}")
                return False
            print(f"[訪問回数] spot_id={spot_id} → {current + 1}回目")
            return True
    except Exception as e:
        print(f"[訪問回数エラー] {e}")
        return False


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


async def should_generate_ai_news_today() -> bool:
    """
    AI情報ダイジェストを生成すべきかどうかを判定する。
    Render無料プランはスリープするため、APSchedulerのcronに固定時刻で頼らず、
    calendar_prep_job と同じ方式（[INITIAL_GREETING] のたびにチェックする）で運用する。

    2026-06-29、コスト削減のため日次（毎日1回）から週次（7日に1回）へ変更した。
    関数名は呼び出し元（main.py）との互換性のため変更していないが、実際の判定基準は
    「最新のdigest作成日から7日以上経過しているか」になっている。
    1件も無い場合（初回）はTrue。
    """
    digest = await get_latest_ai_news_digest()
    if not digest:
        return True

    digest_date_str = digest.get("digest_date")
    if not digest_date_str:
        return True

    try:
        digest_date = datetime.strptime(digest_date_str, "%Y-%m-%d").date()
    except ValueError:
        return True

    today_jst = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).date()

    days_since_last = (today_jst - digest_date).days
    return days_since_last >= 7


@tool
async def get_today_ai_news() -> str:
    """
    ユーザーから「今日のAI情報は？」「最近のAIニュースある？」のように
    AI業界の最新情報について聞かれたときに呼ぶツール。
    daily_ai_news_job が週1回（コスト削減のため2026-06-29に日次から変更）保存した
    ダイジェストの中から最新のものを返す。直近7日以内に生成されていない場合でも、
    最新の保存分を返し、その時点の日付を明記する。
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


# ═══════════════════════════════════════════════════════
# 食事記録（孤食ロボット機能）
# 「ご飯食べた」「コンビニで弁当買った」のような発話を検知して記録し、
# ①振り返って話す ②食事時間帯にプロアクティブに声をかける ③ゆるい健康アドバイスをする
# という3つの体験を支える土台。
# ═══════════════════════════════════════════════════════

MEAL_KEYWORDS = [
    "ご飯", "ごはん", "食べた", "食べる", "食事", "ランチ", "朝食", "昼食", "夕食", "晩飯",
    "弁当", "コンビニ", "外食", "自炊", "作った", "レンジ", "出前", "デリバリー", "おやつ",
]


def detect_meal_mention(user_text: str) -> bool:
    """ユーザー発話に食事関連のキーワードが含まれるかどうかを判定する。"""
    return any(kw in user_text for kw in MEAL_KEYWORDS)


async def save_meal_log(
    description: str,
    meal_type: str | None = None,
    is_alone: bool | None = None,
    healthiness: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    image_url: str | None = None,
) -> bool:
    """
    1件の食事記録を保存する。description以外は分かる範囲でよい（不明ならNone）。
    image_url は📷ボタンで食事を撮った場合のみ渡される（テキストのみの記録ではNone）。
    """
    url, key = _sb()
    if not url or not key:
        return False

    endpoint = f"{url}/rest/v1/meal_logs"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    data: dict = {
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if meal_type:
        data["meal_type"] = meal_type
    if is_alone is not None:
        data["is_alone"] = is_alone
    if healthiness:
        data["healthiness"] = healthiness
    if lat is not None and lng is not None:
        data["lat"] = lat
        data["lng"] = lng
    if image_url:
        data["image_url"] = image_url

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[食事記録] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[食事記録] 保存しました: {description[:30]}")
        return True
    except Exception as e:
        print(f"[食事記録エラー] {e}")
        return False


async def get_recent_meal_logs(limit: int = 7) -> list:
    """直近の食事記録を新しい順に取得する。傾向の振り返り・アドバイス生成に使う。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/meal_logs"
        f"?order=created_at.desc&limit={limit}"
        f"&select=meal_type,description,is_alone,healthiness,image_url,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[食事記録取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[食事記録取得エラー] {e}")
        return []


def build_meal_context(logs: list) -> str:
    """
    直近の食事記録をプロンプト用の短い文字列に整形する。
    1件も無ければ空文字（プロンプトに何も追加しない）。
    """
    if not logs:
        return ""

    JST = timezone(timedelta(hours=9))
    lines = []
    alone_count = 0
    junk_count  = 0

    for log in logs:
        try:
            dt = datetime.fromisoformat(log["created_at"]).astimezone(JST)
            time_str = dt.strftime("%m/%d %H:%M")
        except (KeyError, ValueError, TypeError):
            time_str = ""
        desc = log.get("description", "")
        alone_mark = "（一人）" if log.get("is_alone") else ""
        # episode_memories の get_recent_episodes と同じ [image:URL] 形式で埋め込む。
        # agents/nodes.py 側はこの形式を認識して ||SHOW_IMAGE:URL|| タグの判断材料にできる。
        image_mark = f" [image:{log['image_url']}]" if log.get("image_url") else ""
        lines.append(f"- {time_str} {desc}{alone_mark}{image_mark}")

        if log.get("is_alone"):
            alone_count += 1
        if log.get("healthiness") == "junk":
            junk_count += 1

    summary_note = ""
    if alone_count >= 3:
        summary_note += "（最近、一人で食べている記録が多い）"
    if junk_count >= 3:
        summary_note += "（最近、コンビニ・外食が続いている）"

    return "【最近の食事記録】" + summary_note + "\n" + "\n".join(lines)


async def should_check_meal_reminder(meal_type: str) -> bool:
    """
    その日の meal_type（"breakfast"/"lunch"/"dinner"）の記録がまだ無いかを判定する。
    食事時間帯のプロアクティブ発話（「お昼食べた？」等）を、同じ食事について
    1日に何度も繰り返さないようにするためのガード。
    """
    url, key = _sb()
    if not url or not key:
        return False

    JST = timezone(timedelta(hours=9))
    today_start = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc).isoformat()

    endpoint = (
        f"{url}/rest/v1/meal_logs"
        f"?meal_type=eq.{meal_type}&created_at=gte.{today_start_utc}"
        f"&select=id&limit=1"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            return False
        rows = res.json()
        return len(rows) == 0  # 今日まだ記録が無ければ True（声をかけてよい）
    except Exception as e:
        print(f"[食事リマインダー判定エラー] {e}")
        return False


