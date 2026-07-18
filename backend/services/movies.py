# services/movies.py
# ─────────────────────────────────────────────────────────────────────────────
# 映画通帳機能を担当するサービスモジュール。
#   - タイトル → 映画情報取得（TMDb API v4 Bearer認証。日本語優先、無料枠のみで運用）
#   - movie_logs への保存・取得
#
# TMDb認証はv3（?api_key=xxx）ではなくv4（Authorization: Bearer <token>）を採用。
# v3キーはURLパラメータに乗るためサーバーログ等に残るリスクがあるのに対し、
# v4トークンはヘッダー経由でしか送られずTMDb公式も推奨方式としている（2026-07-17）。
#
# books.py（読書通帳）と同じ設計思想・作法を踏襲している：
#   - Supabaseアクセスは services/memory.py の _sb() を再利用し、httpxでPostgRESTを直接叩く
#   - apikeyヘッダーのみを送る（Authorization: Bearerは新形式キーでゲートウェイに拒否されるため）
#
# books.py との最大の違いは記帳の起点。読書通帳はバーコード（ISBN）スキャンという
# 明示的なUI操作が起点だったが、映画は「〇〇観た」という会話が起点になるため、
# 書き込み（記帳）自体を会話用Tool（log_watched_movie）として実装している。
# バーコード（Blu-ray/DVDのUPC/EAN）からの記帳は、TMDbがバーコード検索に対応しておらず
# 別途バーコード商品DBの精度が不安定なため、今回は実装を見送り将来の拡張とする。
# ─────────────────────────────────────────────────────────────────────────────
import os
from datetime import date, datetime, timezone
from urllib.parse import quote

import httpx
from langchain_core.tools import tool

from services.memory import _sb  # 既存の接続情報ヘルパーを再利用

_TMDB_BASE = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


def _tmdb_headers() -> dict | None:
    """
    TMDb v4認証（APIリードアクセストークン）用のヘッダー。
    v3の?api_key=xxxはURLパラメータに乗るためログに残るリスクがあり、
    TMDb公式もBearer token（v4）を推奨しているため、こちらを正式採用している
    （2026-07-17、v3キーからv4トークンへ切り替え）。
    """
    token = os.getenv("TMDB_API_READ_ACCESS_TOKEN")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _movies_headers() -> dict:
    """
    movie_logs用のヘッダー。books.pyの_books_headers()と同じ理由で、
    apikeyヘッダーのみを送る（Supabase新形式キー対応）。
    """
    _, key = _sb()
    return {"apikey": key}


# ═══════════════════════════════════════════════════════
# タイトル → 映画情報取得（TMDb）
# ═══════════════════════════════════════════════════════

async def fetch_movie_by_title(title: str) -> dict | None:
    """
    タイトルからTMDbを検索し、最も一致度の高い1件の詳細情報を返す。
    日本語タイトルでの検索を優先する（language=ja-JP）。
    TMDB_API_READ_ACCESS_TOKEN環境変数が無い場合、またはヒットしない場合はNoneを返す。
    """
    headers = _tmdb_headers()
    if not headers:
        print("[映画通帳] TMDB_API_READ_ACCESS_TOKENが設定されていません")
        return None

    search_endpoint = f"{_TMDB_BASE}/search/movie"
    params = {"query": title, "language": "ja-JP"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(search_endpoint, headers=headers, params=params, timeout=8.0)
        if res.status_code != 200:
            print(f"[映画通帳][TMDb検索] status={res.status_code}")
            return None
        results = res.json().get("results") or []
    except Exception as e:
        print(f"[映画通帳][TMDb検索エラー] {e}")
        return None

    if not results:
        print(f"[映画通帳] TMDb検索ヒットなし: {title}")
        return None

    # 最初の1件（TMDbの人気度順ソートが概ね信頼できるため、あいまい検索の割り切りとして採用）
    movie_id = results[0].get("id")
    return await _fetch_movie_detail(movie_id)


async def _fetch_movie_detail(movie_id: int) -> dict | None:
    """
    movie_id から詳細情報（監督・ジャンル・あらすじ・ポスター）を取得する。
    credits（クレジット情報）を同時取得し、監督（Director）を抽出する。
    """
    headers = _tmdb_headers()
    if not headers:
        return None
    endpoint = f"{_TMDB_BASE}/movie/{movie_id}"
    params = {"language": "ja-JP", "append_to_response": "credits"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=headers, params=params, timeout=8.0)
        if res.status_code != 200:
            print(f"[映画通帳][TMDb詳細] status={res.status_code}")
            return None
        d = res.json()
    except Exception as e:
        print(f"[映画通帳][TMDb詳細エラー] {e}")
        return None

    title = d.get("title") or d.get("original_title")
    if not title:
        return None

    release_date = d.get("release_date") or ""
    release_year = int(release_date[:4]) if release_date[:4].isdigit() else None

    genres = ", ".join(g["name"] for g in d.get("genres", [])) or None

    poster_path = d.get("poster_path")
    poster_url = f"{_TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

    crew = (d.get("credits") or {}).get("crew", [])
    director = next((c.get("name") for c in crew if c.get("job") == "Director"), None)

    return {
        "tmdb_id": movie_id,
        "title": title,
        "original_title": d.get("original_title"),
        "director": director,
        "genre": genres,
        "release_year": release_year,
        "overview": d.get("overview") or None,
        "poster_url": poster_url,
    }


# ═══════════════════════════════════════════════════════
# movie_logs への保存・取得
# ═══════════════════════════════════════════════════════

async def _find_existing_log(tmdb_id: int | None, title: str) -> dict | None:
    """
    同じ映画が既に記帳済みかどうかを調べる。
    tmdb_idがあれば完全一致、無ければタイトル完全一致で判定する。
    複数件ヒットした場合は最新（created_at降順の先頭）の1件を返す。
    """
    url, key = _sb()
    if not url or not key:
        return None

    if tmdb_id:
        endpoint = (
            f"{url}/rest/v1/movie_logs"
            f"?tmdb_id=eq.{tmdb_id}"
            f"&order=created_at.desc&limit=1"
            f"&select=id,watch_count,watched_at"
        )
    else:
        encoded_title = quote(title, safe="")
        endpoint = (
            f"{url}/rest/v1/movie_logs"
            f"?title=eq.{encoded_title}"
            f"&order=created_at.desc&limit=1"
            f"&select=id,watch_count,watched_at"
        )

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_movies_headers(), timeout=5.0)
        if res.status_code != 200:
            return None
        rows = res.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[映画通帳] 既存記録の確認エラー: {e}")
        return None


async def save_movie_log(movie: dict, watched_at: date | None = None) -> bool:
    """
    映画情報を movie_logs に保存する。
    同じ映画（tmdb_id一致、無ければタイトル完全一致）が既に記帳済みの場合は
    新規行を作らず、既存行の watch_count を+1し、watched_at を最新の日付に更新する
    （再視聴の記帳として扱う）。
    movie は fetch_movie_by_title() の返り値を想定。
    watched_at省略時は今日の日付。
    """
    url, key = _sb()
    if not url or not key:
        return False

    title = movie.get("title")
    if not title:
        print("[映画通帳] titleが無いため保存をスキップしました")
        return False

    new_watched_at = (watched_at or date.today()).isoformat()

    existing = await _find_existing_log(movie.get("tmdb_id"), title)
    if existing:
        return await _increment_watch_count(existing, new_watched_at, title)

    endpoint = f"{url}/rest/v1/movie_logs"
    headers = {**_movies_headers(), "Content-Type": "application/json"}
    data: dict = {
        "title": title,
        "watched_at": new_watched_at,
        "source": "conversation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        # watch_count は列のDEFAULT 1に任せる
    }
    if movie.get("tmdb_id") is not None:
        data["tmdb_id"] = movie["tmdb_id"]
    if movie.get("original_title"):
        data["original_title"] = movie["original_title"]
    if movie.get("director"):
        data["director"] = movie["director"]
    if movie.get("genre"):
        data["genre"] = movie["genre"]
    if movie.get("release_year") is not None:
        data["release_year"] = movie["release_year"]
    if movie.get("overview"):
        data["overview"] = movie["overview"]
    if movie.get("poster_url"):
        data["poster_url"] = movie["poster_url"]

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[映画通帳] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[映画通帳] 新規記帳しました: {title[:30]}")
        return True
    except Exception as e:
        print(f"[映画通帳エラー] {e}")
        return False


async def _increment_watch_count(existing: dict, new_watched_at: str, title: str) -> bool:
    """既存行のwatch_countを+1し、watched_atを最新の日付に更新する（PATCH）。"""
    url, key = _sb()
    if not url or not key:
        return False

    row_id = existing["id"]
    current_count = existing.get("watch_count") or 1
    endpoint = f"{url}/rest/v1/movie_logs?id=eq.{row_id}"
    headers = {**_movies_headers(), "Content-Type": "application/json"}
    data = {
        "watch_count": current_count + 1,
        "watched_at": new_watched_at,
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.patch(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 204):
            print(f"[映画通帳] 再視聴の更新失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[映画通帳] 再視聴を記帳しました（{current_count + 1}回目）: {title[:30]}")
        return True
    except Exception as e:
        print(f"[映画通帳エラー] {e}")
        return False


async def get_recent_movie_logs(limit: int = 10) -> list:
    """直近の映画記録を新しい順に取得する。会話Tool・映画通帳ページの両方から使う想定。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/movie_logs"
        f"?order=created_at.desc&limit={limit}"
        f"&select=id,tmdb_id,title,original_title,director,genre,release_year,overview,poster_url,watched_at,created_at,watch_count"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_movies_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[映画通帳取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[映画通帳取得エラー] {e}")
        return []


async def find_movie_log_by_title(query: str, limit: int = 5) -> list:
    """
    タイトルの部分一致（ilike）で映画記録を検索する。
    「あの映画いつ観た？」のように作品名で聞かれたときに使う。
    """
    url, key = _sb()
    if not url or not key:
        return []

    encoded_query = query.replace(" ", "%20")
    endpoint = (
        f"{url}/rest/v1/movie_logs"
        f"?title=ilike.*{encoded_query}*"
        f"&order=created_at.desc&limit={limit}"
        f"&select=id,tmdb_id,title,original_title,director,genre,release_year,overview,poster_url,watched_at,created_at,watch_count"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_movies_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[映画通帳検索エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[映画通帳検索エラー] {e}")
        return []


async def get_movie_stats() -> dict:
    """累計本数・監督の重複無しリストを返す（「今まで何本観た？」系の会話Tool用）。"""
    url, key = _sb()
    if not url or not key:
        return {"count": 0}

    endpoint = f"{url}/rest/v1/movie_logs?select=director"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_movies_headers(), timeout=5.0)
        if res.status_code != 200:
            return {"count": 0}
        rows = res.json()
    except Exception as e:
        print(f"[映画通帳集計エラー] {e}")
        return {"count": 0}

    return {"count": len(rows)}


# ═══════════════════════════════════════════════════════
# 会話中のTool（get_book_history と同型の読み取り専用Tool、
# それに加えて書き込み用の log_watched_movie を持つ）
# ═══════════════════════════════════════════════════════

@tool
async def log_watched_movie(title: str) -> str:
    """
    ユーザーが「〇〇観た」「〇〇を見た」のように映画を観たことを話したときに呼ぶツール。
    titleには作品名をそのまま入れる（読み仮名変換や英訳はしない）。
    TMDbで照会し、見つかった映画情報をmovie_logsに記帳する。
    ヒットしなかった場合は、その旨を伝える文字列を返す（記帳は行わない）。
    雑談や、映画を観た事実が無い発言では絶対に呼ばないこと。
    """
    movie = await fetch_movie_by_title(title.strip())
    if not movie:
        return f"「{title}」という映画がTMDbで見つかりませんでした。記帳はしていません。"

    saved = await save_movie_log(movie)
    if not saved:
        return f"「{movie['title']}」は見つかりましたが、記帳に失敗しました。"

    director_part = f"監督: {movie['director']}" if movie.get("director") else ""
    year_part = f"（{movie['release_year']}年）" if movie.get("release_year") else ""
    genre_part = f" ジャンル: {movie['genre']}" if movie.get("genre") else ""
    return (
        f"「{movie['title']}」{year_part}を記帳しました。{director_part}{genre_part}\n"
        f"あらすじ: {(movie.get('overview') or '（あらすじ情報なし）')[:100]}"
    )


@tool
async def get_movie_history(query: str = "") -> str:
    """
    ユーザーから鑑賞した映画の記録について聞かれたときに呼ぶツール。
    「あの映画いつ観た？」のような特定のタイトルを含む質問にはqueryに
    そのタイトル（または分かる範囲のキーワード）を入れて呼ぶ。
    「最近何本観た？」「今まで何本観た？」のような集計・一覧系の質問には
    queryを空のまま呼ぶ。
    該当する記録が無い場合は「記録にはないみたい」という旨の文字列を返す。
    """
    if query.strip():
        logs = await find_movie_log_by_title(query.strip())
        if not logs:
            return f"「{query}」に該当する映画記録は見当たりません。"
        lines = []
        for log in logs[:5]:
            watched = log.get("watched_at", "")
            title = log.get("title", "")
            director = log.get("director", "")
            director_part = f"（{director}）" if director else ""
            count = log.get("watch_count") or 1
            count_part = f"・{count}回目" if count > 1 else ""
            genre = log.get("genre", "")
            genre_part = f"・{genre}" if genre else ""
            lines.append(f"- {watched} {title}{director_part}{count_part}{genre_part}")
        return "見つかった映画記録:\n" + "\n".join(lines)

    stats = await get_movie_stats()
    if stats["count"] == 0:
        return "まだ映画の記録はありません。"

    recent = await get_recent_movie_logs(limit=5)
    lines = []
    for log in recent:
        watched = log.get("watched_at", "")
        title = log.get("title", "")
        count = log.get("watch_count") or 1
        count_part = f"・{count}回目" if count > 1 else ""
        lines.append(f"- {watched} {title}{count_part}")

    summary = f"これまでの記録: 累計{stats['count']}本\n\n直近の記録:\n" + "\n".join(lines)
    return summary
