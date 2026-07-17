# services/timeline.py
# ─────────────────────────────────────────────────────────────────────────────
# 「一緒に過ごした時間」を時系列で振り返るためのタイムライン集約サービス。
# episode_memories（写真付きの記憶）・video_prompts（採用済み動画）・
# ruki_mind/月次マインドプロファイル を横断して1本のタイムラインにまとめる。
# アルバムUI（public/tools/album_ui.html）から呼ばれる、閲覧専用の機能。
# ─────────────────────────────────────────────────────────────────────────────
import glob
import re
from pathlib import Path

import httpx

from services.memory import _sb, _sb_headers
from services.character_bible import _RUKI_MIND_DIR


async def _fetch_episode_photos(limit: int = 30) -> list[dict]:
    """episode_memoriesのうち、image_urlがある行だけを写真として抽出する。"""
    base_url, _ = _sb()
    endpoint = f"{base_url}/rest/v1/episode_memories"
    params = {
        "select": "created_at,summary,mood_at_time,image_url,visual_description",
        "image_url": "not.is.null",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(endpoint, headers=_sb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()

    return [
        {
            "type": "photo",
            "date": r.get("created_at"),
            "title": r.get("summary") or "",
            "detail": r.get("visual_description") or "",
            "mood": r.get("mood_at_time") or "",
            "media_url": r.get("image_url"),
        }
        for r in rows
    ]


async def _fetch_adopted_videos(limit: int = 30) -> list[dict]:
    """video_promptsのうち、実際に採用された結果動画があるものだけを抽出する。"""
    base_url, _ = _sb()
    endpoint = f"{base_url}/rest/v1/video_prompts"
    params = {
        "select": "created_at,tested_at,genre,prompt_ja,result_video_url,status",
        "result_video_url": "not.is.null",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(endpoint, headers=_sb_headers(), params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()

    return [
        {
            "type": "video",
            "date": r.get("tested_at") or r.get("created_at"),
            "title": (r.get("prompt_ja") or "")[:50],
            "detail": r.get("genre") or "",
            "mood": "",
            "media_url": r.get("result_video_url"),
        }
        for r in rows
    ]


def _fetch_mind_profile_milestones() -> list[dict]:
    """
    ruki_mind/YYYY-MM.md の一覧から、月次マインドプロファイルの更新を
    タイムライン上の「成長の節目」として拾う。ファイルの中身までは読まず、
    存在する月だけを一覧化する（軽量に保つため）。
    """
    pattern = str(_RUKI_MIND_DIR / "20*-*.md")
    paths = sorted(glob.glob(pattern))
    items = []
    for p in paths:
        name = Path(p).stem  # 例: "2026-07"
        m = re.match(r"(\d{4})-(\d{2})", name)
        if not m:
            continue
        year, month = m.group(1), m.group(2)
        items.append({
            "type": "growth",
            "date": f"{year}-{month}-01T00:00:00+00:00",
            "title": f"{year}年{month}月のルキルキ",
            "detail": "マインドプロファイルが更新されました",
            "mood": "",
            "media_url": None,
        })
    return items


async def get_timeline(limit: int = 40) -> list[dict]:
    """
    写真・動画・成長の節目を時系列（新しい順）で統合したタイムラインを返す。
    アルバムUIはこれを1回呼ぶだけで全種類の「思い出」を時系列表示できる。
    """
    photos = await _fetch_episode_photos(limit)
    videos = await _fetch_adopted_videos(limit)
    growth = _fetch_mind_profile_milestones()

    merged = photos + videos + growth
    merged.sort(key=lambda x: x.get("date") or "", reverse=True)
    return merged[:limit]
