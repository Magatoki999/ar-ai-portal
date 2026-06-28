# services/books.py
# ─────────────────────────────────────────────────────────────────────────────
# 読書通帳機能を担当するサービスモジュール。
#   - ISBN → 書誌情報取得（NDLサーチAPI優先、ヒットしなければGoogle Booksにフォールバック）
#   - reading_logs への保存・取得
#
# 当初openBD APIを第一候補にする想定だったが、openBDは2023年7月に
# 「openBD API（バージョン1）」の提供終了が発表されており（JPROとのデータ提供契約上の
# 問題が原因）、現在は新刊データの更新が止まっている可能性が高い。実機テストでも
# ヒットしなかったため、現役で稼働している国立国会図書館サーチAPI（NDLサーチ・
# OpenSearch形式・APIキー不要）を第一候補に変更している。
#
# Supabaseへのアクセスは services/memory.py と同じ作法（httpxでPostgRESTを直接叩く）に揃えている。
# supabase-pyクライアントは使わない。
# ─────────────────────────────────────────────────────────────────────────────
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import httpx
from langchain_core.tools import tool

from services.memory import _sb  # 既存の接続情報ヘルパーを再利用


def _books_headers() -> dict:
    """
    reading_logs用のヘッダー。memory.py の _sb_headers() は
    Authorization: Bearer {key} も同時に送るが、Supabaseの新形式キー
    （sb_secret_... ・JWTではない）はAuthorizationヘッダーに乗せると
    ゲートウェイに拒否される（実機検証済み・2026-06-28）。
    apikeyヘッダーのみを送ればゲートウェイが内部で正しく解釈してくれるため、
    books.py ではこちらを使う。
    既存の memory.py 側（_sb_headers()）はレガシーキー運用中の他機能に
    影響が出る可能性があるため、今回は意図的に変更していない。
    """
    _, key = _sb()
    return {"apikey": key}


_NDL_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
}


# ═══════════════════════════════════════════════════════
# ISBN → 書誌情報取得
# ═══════════════════════════════════════════════════════

async def fetch_book_by_isbn(isbn: str) -> dict | None:
    """
    ISBNから書誌情報（title/author/publisher/cover_url）を取得する。
    NDLサーチAPI（国立国会図書館・日本の書誌データに特化・APIキー不要）
    → Google Books の順に試行し、両方失敗したらNoneを返す。
    どちらのソースも定価（price）は基本的に持たないため、price は別途
    手入力で補完する前提（記帳モーダルで編集可能にする）。
    """
    isbn = isbn.strip().replace("-", "")

    book = await _fetch_from_ndl(isbn)
    if book:
        return book

    book = await _fetch_from_google_books(isbn)
    if book:
        return book

    print(f"[読書通帳] ISBN照会失敗（NDLサーチ/Google Books両方ヒットなし）: {isbn}")
    return None


async def _fetch_from_ndl(isbn: str) -> dict | None:
    """
    国立国会図書館サーチ OpenSearch API。レスポンスはXML(RSS)形式で返る。
    1つのISBNに対して複数itemが返ることがある仕様のため、最初の有効な1件を使う。
    """
    endpoint = f"https://ndlsearch.ndl.go.jp/api/opensearch?isbn={isbn}"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, timeout=8.0)
        if res.status_code != 200:
            print(f"[読書通帳][NDLサーチ] status={res.status_code}")
            return None
        root = ET.fromstring(res.text)
    except Exception as e:
        print(f"[読書通帳][NDLサーチエラー] {e}")
        return None

    item = root.find("./channel/item")
    if item is None:
        return None

    def _text(tag: str) -> str | None:
        el = item.find(tag, _NDL_NS)
        return el.text.strip() if el is not None and el.text else None

    title = _text("title")
    if not title:
        return None

    return {
        "isbn": isbn,
        "title": title,
        "author": _text("dc:creator") or _text("author"),
        "publisher": _text("dc:publisher"),
        "price": None,  # NDLサーチは定価を提供しない
        "cover_url": None,  # 書影APIは別エンドポイント。2026年に縮小方針が出ているため当面非対応
    }


async def _fetch_from_google_books(isbn: str) -> dict | None:
    """
    Google Books API。APIキー無し運用だとレート制限（429）に当たりやすいので、
    あくまでNDLサーチでヒットしなかった場合のみの保険として位置づける。
    """
    endpoint = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, timeout=8.0)
        if res.status_code == 429:
            print("[読書通帳][GoogleBooks] レート制限(429)。手入力にフォールバックしてください")
            return None
        if res.status_code != 200:
            print(f"[読書通帳][GoogleBooks] status={res.status_code}")
            return None
        data = res.json()
    except Exception as e:
        print(f"[読書通帳][GoogleBooksエラー] {e}")
        return None

    items = data.get("items")
    if not items:
        return None

    info = items[0].get("volumeInfo", {})
    title = info.get("title")
    if not title:
        return None

    return {
        "isbn": isbn,
        "title": title,
        "author": ", ".join(info.get("authors", [])) or None,
        "publisher": info.get("publisher"),
        "price": None,  # Google Books APIは定価を持たないことが多い
        "cover_url": info.get("imageLinks", {}).get("thumbnail"),
    }


# ═══════════════════════════════════════════════════════
# reading_logs への保存・取得
# ═══════════════════════════════════════════════════════

async def save_reading_log(
    book: dict,
    borrowed_at: date | None = None,
) -> bool:
    """
    書誌情報を1件、reading_logsに保存する。
    book は fetch_book_by_isbn() の返り値（isbn/title/author/publisher/price/cover_url）を想定。
    borrowed_at省略時は今日の日付。
    """
    url, key = _sb()
    if not url or not key:
        return False

    title = book.get("title")
    if not title:
        print("[読書通帳] titleが無いため保存をスキップしました")
        return False

    endpoint = f"{url}/rest/v1/reading_logs"
    headers = {**_books_headers(), "Content-Type": "application/json"}
    data: dict = {
        "title": title,
        "borrowed_at": (borrowed_at or date.today()).isoformat(),
        "source": "library",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if book.get("isbn"):
        data["isbn"] = book["isbn"]
    if book.get("author"):
        data["author"] = book["author"]
    if book.get("publisher"):
        data["publisher"] = book["publisher"]
    if book.get("price") is not None:
        data["price"] = book["price"]
    if book.get("cover_url"):
        data["cover_url"] = book["cover_url"]

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[読書通帳] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[読書通帳] 保存しました: {title[:30]}")
        return True
    except Exception as e:
        print(f"[読書通帳エラー] {e}")
        return False


async def get_recent_reading_logs(limit: int = 10) -> list:
    """直近の読書記録を新しい順に取得する。会話Tool・通帳ページの両方から使う想定。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/reading_logs"
        f"?order=created_at.desc&limit={limit}"
        f"&select=id,isbn,title,author,publisher,price,cover_url,borrowed_at,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_books_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[読書通帳取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[読書通帳取得エラー] {e}")
        return []


async def find_reading_log_by_title(query: str, limit: int = 5) -> list:
    """
    タイトルの部分一致（ilike）で読書記録を検索する。
    「あの本いつ借りた？」のように作品名で聞かれたときに使う。
    """
    url, key = _sb()
    if not url or not key:
        return []

    encoded_query = query.replace(" ", "%20")
    endpoint = (
        f"{url}/rest/v1/reading_logs"
        f"?title=ilike.*{encoded_query}*"
        f"&order=created_at.desc&limit={limit}"
        f"&select=id,isbn,title,author,publisher,price,cover_url,borrowed_at,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_books_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[読書通帳検索エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[読書通帳検索エラー] {e}")
        return []


async def get_reading_stats() -> dict:
    """
    累計冊数・累計金額を返す。通帳ページのダッシュボードや
    「今まで何冊読んだ？」系の会話Toolから使う想定。
    件数が多くなってきたら専用のSQL集計（RPC）に切り替えるのが理想だが、
    現状の利用規模ではこの実装で十分。
    """
    url, key = _sb()
    if not url or not key:
        return {"count": 0, "total_price": 0}

    endpoint = f"{url}/rest/v1/reading_logs?select=price"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_books_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[読書通帳集計エラー] status={res.status_code} body={res.text[:200]}")
            return {"count": 0, "total_price": 0}
        rows = res.json()
    except Exception as e:
        print(f"[読書通帳集計エラー] {e}")
        return {"count": 0, "total_price": 0}

    count = len(rows)
    total_price = sum(r.get("price") or 0 for r in rows)
    return {"count": count, "total_price": total_price}


# ═══════════════════════════════════════════════════════
# 会話中の質問応答用Tool（get_my_schedule / get_today_ai_news と同型）
# ═══════════════════════════════════════════════════════

@tool
async def get_book_history(query: str = "") -> str:
    """
    ユーザーから読書記録について聞かれたときに呼ぶツール。
    「いつその本借りた？」のような特定の本のタイトルを含む質問にはqueryに
    そのタイトル（または分かる範囲のキーワード）を入れて呼ぶ。
    「最近何冊読んだ？」「今まで何冊借りた？」「合計いくら分読んだ？」のような
    集計・一覧系の質問にはqueryを空のまま呼ぶ。
    該当する記録が無い場合は「記録にはないみたい」という旨の文字列を返す。
    """
    if query.strip():
        logs = await find_reading_log_by_title(query.strip())
        if not logs:
            return f"「{query}」に該当する読書記録は見当たりません。"
        lines = []
        for log in logs[:5]:
            borrowed = log.get("borrowed_at", "")
            title = log.get("title", "")
            author = log.get("author", "")
            author_part = f"（{author}）" if author else ""
            lines.append(f"- {borrowed} {title}{author_part}")
        return "見つかった読書記録:\n" + "\n".join(lines)

    stats = await get_reading_stats()
    if stats["count"] == 0:
        return "まだ読書記録はありません。"

    recent = await get_recent_reading_logs(limit=5)
    lines = []
    for log in recent:
        borrowed = log.get("borrowed_at", "")
        title = log.get("title", "")
        lines.append(f"- {borrowed} {title}")

    price_part = f"・合計{stats['total_price']}円分" if stats["total_price"] else ""
    summary = f"これまでの記録: 累計{stats['count']}冊{price_part}\n\n直近の記録:\n" + "\n".join(lines)
    return summary
