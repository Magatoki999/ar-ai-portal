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
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from urllib.parse import quote

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
    "dcterms": "http://purl.org/dc/terms/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
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

    2026-06-28、NDLサーチヒット時にもGoogle Booksへ表紙補完の追加照会を行う
    実装を一時的に試したが、Google Books APIキー無し運用がレート制限(429)に
    恒常的に当たることが実機検証で判明したため撤去した。cover_url列は
    将来また別の書影ソースを試す可能性に備えてテーブル上は残している。
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

    def _text_or_rdf_value(tag: str) -> str | None:
        """
        DC-NDL仕様では <dcndl:seriesTitle><rdf:Description><rdf:value>値</rdf:value>...
        のような入れ子構造になる場合があるが、データプロバイダによっては
        <dcndl:seriesTitle>値</dcndl:seriesTitle> という素のテキストで返る場合もある
        （2026-06-28、実機検証で両方のパターンを確認）。両方に対応する。
        """
        el = item.find(tag, _NDL_NS)
        if el is None:
            return None
        if el.text and el.text.strip():
            return el.text.strip()
        # 入れ子構造（rdf:Description/rdf:value）を探す
        value_el = el.find("./rdf:Description/rdf:value", _NDL_NS)
        if value_el is not None and value_el.text:
            return value_el.text.strip()
        return None

    title = _text("title")
    if not title:
        return None

    # 出版年：dcterms:issued（"2024-02"等）→ なければ dc:date の順で試す。
    # 表示用に先頭4桁（西暦）だけ抜き出す。
    raw_year = _text("dcterms:issued") or _text("dc:date")
    published_year = None
    if raw_year:
        digits = re.match(r"(\d{4})", raw_year)
        if digits:
            published_year = int(digits.group(1))

    return {
        "isbn": isbn,
        "title": title,
        "author": _text("dc:creator") or _text("author"),
        "publisher": _text("dc:publisher"),
        "price": None,  # NDLサーチは定価を提供しない
        "cover_url": None,  # NDLサーチは書影を提供しない。Google Booksへの追加照会による
                             # 補完も2026-06-28に試したがレート制限(429)が頻発したため撤去済み。
                             # reading_logs.cover_url列は将来の別ソース対応に備えて残してある。
        "genre": _text("dcndl:genre"),
        "series_title": _text_or_rdf_value("dcndl:seriesTitle"),
        "volume": _text_or_rdf_value("dcndl:volume"),
        "published_year": published_year,
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

    # Google Books の publishedDate は "2021" や "2021-07-15" 等の形式。先頭4桁を年として使う。
    published_year = None
    raw_date = info.get("publishedDate")
    if raw_date:
        digits = re.match(r"(\d{4})", raw_date)
        if digits:
            published_year = int(digits.group(1))

    return {
        "isbn": isbn,
        "title": title,
        "author": ", ".join(info.get("authors", [])) or None,
        "publisher": info.get("publisher"),
        "price": None,  # Google Books APIは定価を持たないことが多い
        "cover_url": info.get("imageLinks", {}).get("thumbnail"),
        # Google Booksの categories はジャンルというよりNDC的な分類タグの場合もあるが、
        # NDLサーチのdcndl:genreが取れなかった場合の代替として一応保持しておく。
        "genre": ", ".join(info.get("categories", [])) or None,
        "series_title": None,  # Google Books APIには対応するフィールドが無い
        "volume": None,        # 同上
        "published_year": published_year,
    }


# ═══════════════════════════════════════════════════════
# reading_logs への保存・取得
# ═══════════════════════════════════════════════════════

async def _find_existing_log(isbn: str | None, title: str) -> dict | None:
    """
    同じ本が既に記帳済みかどうかを調べる。
    ISBNがあればISBN完全一致、無ければタイトル完全一致（部分一致ではない）で判定する。
    複数件ヒットした場合は最新（created_at降順の先頭）の1件を返す。
    """
    url, key = _sb()
    if not url or not key:
        return None

    if isbn:
        # PostgRESTのeq演算子はURLエンコードされた値を渡す必要がある
        encoded_isbn = quote(isbn, safe="")
        endpoint = (
            f"{url}/rest/v1/reading_logs"
            f"?isbn=eq.{encoded_isbn}"
            f"&order=created_at.desc&limit=1"
            f"&select=id,borrow_count,borrowed_at"
        )
    else:
        # ISBN無し（手入力でISBNを入れなかった等）の場合はタイトル完全一致で代替判定。
        # find_reading_log_by_title（部分一致）とは別の厳密一致クエリ。
        encoded_title = quote(title, safe="")
        endpoint = (
            f"{url}/rest/v1/reading_logs"
            f"?title=eq.{encoded_title}"
            f"&order=created_at.desc&limit=1"
            f"&select=id,borrow_count,borrowed_at"
        )

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_books_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[読書通帳] 既存記録の確認に失敗: status={res.status_code}")
            return None
        rows = res.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[読書通帳] 既存記録の確認エラー: {e}")
        return None


async def save_reading_log(
    book: dict,
    borrowed_at: date | None = None,
) -> bool:
    """
    書誌情報を reading_logs に保存する。
    同じ本（ISBN一致、無ければタイトル完全一致）が既に記帳済みの場合は
    新規行を作らず、既存行の borrow_count を+1し、borrowed_at を最新の日付に更新する
    （再読・再度借りた場合の「記帳」として扱う）。
    book は fetch_book_by_isbn() の返り値（isbn/title/author/publisher/price/cover_url/
    genre/series_title/volume/published_year）を想定。
    borrowed_at省略時は今日の日付。
    """
    url, key = _sb()
    if not url or not key:
        return False

    title = book.get("title")
    if not title:
        print("[読書通帳] titleが無いため保存をスキップしました")
        return False

    new_borrowed_at = (borrowed_at or date.today()).isoformat()

    # ── 既存記録があるか確認 ──
    existing = await _find_existing_log(book.get("isbn"), title)
    if existing:
        return await _increment_borrow_count(existing, new_borrowed_at, title)

    # ── 新規作成 ──
    endpoint = f"{url}/rest/v1/reading_logs"
    headers = {**_books_headers(), "Content-Type": "application/json"}
    data: dict = {
        "title": title,
        "borrowed_at": new_borrowed_at,
        "source": "library",
        "created_at": datetime.now(timezone.utc).isoformat(),
        # borrow_count は列のDEFAULT 1に任せる（明示的には送らない）
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
    if book.get("genre"):
        data["genre"] = book["genre"]
    if book.get("series_title"):
        data["series_title"] = book["series_title"]
    if book.get("volume"):
        data["volume"] = book["volume"]
    if book.get("published_year") is not None:
        data["published_year"] = book["published_year"]

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[読書通帳] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[読書通帳] 新規記帳しました: {title[:30]}")
        return True
    except Exception as e:
        print(f"[読書通帳エラー] {e}")
        return False


async def _increment_borrow_count(existing: dict, new_borrowed_at: str, title: str) -> bool:
    """既存行のborrow_countを+1し、borrowed_atを最新の日付に更新する（PATCH）。"""
    url, key = _sb()
    if not url or not key:
        return False

    row_id = existing["id"]
    current_count = existing.get("borrow_count") or 1
    endpoint = f"{url}/rest/v1/reading_logs?id=eq.{row_id}"
    headers = {**_books_headers(), "Content-Type": "application/json"}
    data = {
        "borrow_count": current_count + 1,
        "borrowed_at": new_borrowed_at,
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.patch(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 204):
            print(f"[読書通帳] 再記帳の更新失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[読書通帳] 再記帳しました（{current_count + 1}回目）: {title[:30]}")
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
        f"&select=id,isbn,title,author,publisher,price,cover_url,borrowed_at,created_at,borrow_count,genre,series_title,volume,published_year"
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
        f"&select=id,isbn,title,author,publisher,price,cover_url,borrowed_at,created_at,borrow_count,genre,series_title,volume,published_year"
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


def _short_title(title: str) -> str:
    """
    NDLサーチ等が返す「メインタイトル : サブタイトル」形式から、
    会話で読み上げるのに適したメインタイトルだけを取り出す。
    区切り文字が無ければそのまま返す。
    """
    if not title:
        return title
    for sep in (" : ", "：", ":"):
        if sep in title:
            return title.split(sep, 1)[0].strip()
    return title.strip()


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
            title = _short_title(log.get("title", ""))
            author = log.get("author", "")
            author_part = f"（{author}）" if author else ""
            count = log.get("borrow_count") or 1
            count_part = f"・{count}回目" if count > 1 else ""
            genre = log.get("genre", "")
            genre_part = f"・{genre}" if genre else ""
            lines.append(f"- {borrowed} {title}{author_part}{count_part}{genre_part}")
        return "見つかった読書記録:\n" + "\n".join(lines)

    stats = await get_reading_stats()
    if stats["count"] == 0:
        return "まだ読書記録はありません。"

    recent = await get_recent_reading_logs(limit=5)
    lines = []
    for log in recent:
        borrowed = log.get("borrowed_at", "")
        title = _short_title(log.get("title", ""))
        count = log.get("borrow_count") or 1
        count_part = f"・{count}回目" if count > 1 else ""
        lines.append(f"- {borrowed} {title}{count_part}")

    price_part = f"・合計{stats['total_price']}円分" if stats["total_price"] else ""
    summary = f"これまでの記録: 累計{stats['count']}冊{price_part}\n\n直近の記録:\n" + "\n".join(lines)
    return summary
