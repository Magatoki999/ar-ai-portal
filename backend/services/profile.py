# services/profile.py
# ─────────────────────────────────────────────────────────────────────────────
# ユーザー向け「記憶ベース（マイプロフィール）」集計サービス。
#
# ruki_mind（services/character_bible.py）がルキルキ自身の人格を記録するのに対し、
# こちらは「まがときさんについて、既存のDBから分かること」を横断集計して見せる、
# 対になる機能。新しいテーブルは作らず、既存の各サービスモジュールの関数を
# 再利用するだけで完結する（services/timeline.py が写真・動画・読書・映画の
# 記録を横断集計しているのと同じ設計思想）。
#
# 表示専用（読み取り専用）。書き込み系のロジックはそれぞれの元サービス
# （memory.py / books.py / movies.py）側にあるため、ここには持たせない。
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone

import httpx

from services.memory import _sb, _sb_headers, get_memory_spots, get_recent_meal_logs
from services.books import get_reading_stats, get_recent_reading_logs
from services.movies import get_movie_stats, get_recent_movie_logs
from services.character_bible import get_latest_growth_note

JST = timezone(timedelta(hours=9))


async def _fetch_recent_episode_summaries(limit: int = 6) -> list[dict]:
    """
    episode_memories から直近の出来事の要約だけを軽量に取得する。
    services/timeline.py の _fetch_episode_photos() と違い、image_urlの有無は問わない
    （「最近の出来事」欄は文章での振り返りが主目的で、写真の有無は必須ではないため）。
    """
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/episode_memories"
        f"?select=created_at,summary,keywords"
        f"&order=created_at.desc&limit={limit}"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=8.0)
        if res.status_code != 200:
            print(f"[記憶ベース] エピソード取得失敗: status={res.status_code}")
            return []
        return res.json()
    except Exception as e:
        print(f"[記憶ベース] エピソード取得エラー: {e}")
        return []


def _summarize_meal_trend(logs: list) -> dict:
    """
    services/memory.py の build_meal_context() と同じ集計ロジック（alone_count / junk_count）を、
    プロンプト用の文章ではなく画面表示用の構造化データとして再利用する。
    """
    alone_count = sum(1 for log in logs if log.get("is_alone"))
    junk_count = sum(1 for log in logs if log.get("healthiness") == "junk")
    recent = [
        {"description": log.get("description", ""), "created_at": log.get("created_at")}
        for log in logs[:5]
    ]
    return {
        "total_logged": len(logs),
        "alone_count": alone_count,
        "junk_count": junk_count,
        "recent": recent,
    }


def _format_spot(spot: dict) -> dict:
    return {
        "name": spot.get("name", ""),
        "name_reading": spot.get("name_reading", ""),
        "created_at": spot.get("created_at"),
    }


def _format_reading(log: dict) -> dict:
    return {
        "title": log.get("title", ""),
        "author": log.get("author"),
        "genre": log.get("genre"),
        "borrowed_at": log.get("borrowed_at"),
    }


def _format_movie(log: dict) -> dict:
    return {
        "title": log.get("title", ""),
        "director": log.get("director"),
        "genre": log.get("genre"),
        "watched_at": log.get("watched_at"),
    }


async def build_memory_base(meal_days: int = 30) -> dict:
    """
    「記憶ベース」画面用のデータを一括で組み立てる。
    既存の各サービス関数（memory.py / books.py / movies.py / character_bible.py）を
    呼び出して集約するだけで、新規のDBアクセス・テーブルはほぼ発生しない
    （episode_memoriesへの直接クエリのみ、timeline.pyと同じ作法で新規追加）。
    """
    spots = await get_memory_spots()
    episodes = await _fetch_recent_episode_summaries(limit=6)
    reading_stats = await get_reading_stats()
    recent_books = await get_recent_reading_logs(limit=3)
    movie_stats = await get_movie_stats()
    recent_movies = await get_recent_movie_logs(limit=3)
    meal_logs = await get_recent_meal_logs(limit=100)  # 大きめに取得し日数フィルタは呼び出し側の判断に委ねる

    cutoff = (datetime.now(timezone.utc) - timedelta(days=meal_days)).isoformat()
    meal_logs_in_range = [log for log in meal_logs if (log.get("created_at") or "") >= cutoff]

    growth_note = get_latest_growth_note()

    return {
        "favorite_spots": [_format_spot(s) for s in spots[:8]],
        "recent_events": [
            {
                "summary": e.get("summary", ""),
                "keywords": e.get("keywords", []),
                "created_at": e.get("created_at"),
            }
            for e in episodes
        ],
        "reading": {
            "total_count": reading_stats.get("count", 0),
            "recent": [_format_reading(b) for b in recent_books],
        },
        "movies": {
            "total_count": movie_stats.get("count", 0),
            "recent": [_format_movie(m) for m in recent_movies],
        },
        "meal_trend": _summarize_meal_trend(meal_logs_in_range),
        "growth_note": growth_note,  # ルキルキ視点の「先月との変化」一言（無ければNone）
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
