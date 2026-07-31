# services/user_growth.py
# ─────────────────────────────────────────────────────────────────────────────
# ユーザー自身の成長記録（自己申告ログ）。
#
# services/character_bible.py の generate_growth_note() が「ルキルキ自身の変化」を
# 過去ログからAIが自動推測して生成するのに対し、こちらは向きが逆で
# 「まがときさんが自分から言ったこと」だけを記録する。AIが行動ログから
# 勝手に「成長した」と判定することはしない（本人が言っていないことを
# 言い当てる形になり、的外れになりやすいため）。
#
# 前提となるSupabaseテーブル（未作成の場合はSQL Editorで実行）:
#
#   create table user_growth_notes (
#     id bigint generated always as identity primary key,
#     note text not null,
#     created_at timestamptz not null default now()
#   );
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone

import httpx
from langchain_core.tools import tool

from services.memory import _sb, _sb_headers  # 既存の接続情報ヘルパーを再利用


async def save_user_growth_note(note: str) -> bool:
    """まがときさんが自分から語った成長・自慢を1件保存する。"""
    url, key = _sb()
    if not url or not key:
        return False

    note = note.strip()
    if not note:
        return False

    endpoint = f"{url}/rest/v1/user_growth_notes"
    headers  = {**_sb_headers(), "Content-Type": "application/json"}
    data = {
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, json=data, headers=headers, timeout=5.0)
        if res.status_code not in (200, 201):
            print(f"[成長記録] 保存失敗: status={res.status_code} body={res.text[:200]}")
            return False
        print(f"[成長記録] 記録しました: {note[:40]}")
        return True
    except Exception as e:
        print(f"[成長記録エラー] {e}")
        return False


async def get_recent_growth_notes(limit: int = 10) -> list:
    """最近の自己申告成長記録を新しい順に取得する（memory_base_ui.html・会話Tool双方から利用）。"""
    url, key = _sb()
    if not url or not key:
        return []

    endpoint = (
        f"{url}/rest/v1/user_growth_notes"
        f"?order=created_at.desc&limit={limit}"
        f"&select=id,note,created_at"
    )
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(endpoint, headers=_sb_headers(), timeout=5.0)
        if res.status_code != 200:
            print(f"[成長記録取得エラー] status={res.status_code} body={res.text[:200]}")
            return []
        return res.json()
    except Exception as e:
        print(f"[成長記録取得エラー] {e}")
        return []


# ═══════════════════════════════════════════════════════
# 会話中の登録・確認用Tool（reminders.py の set_reminder / get_my_reminders と同型）
# ═══════════════════════════════════════════════════════

@tool
async def log_user_growth(note: str) -> str:
    """まがときさんが「イラスト描けるようになった気がする」「〇〇のスキル上がった」
    「やっと△△が出来るようになった」のように、自分自身の成長・上達・自慢を
    **自分から語ったとき**にだけ呼ぶツールです。まがときさんの行動ログから
    AIが勝手に成長したと判定して呼ぶことは絶対にしないでください
    （本人が言ってもいないことを記録するのは不自然です）。

    Args:
        note: まがときさんが語った成長・自慢の内容を、本人の言葉を活かして簡潔にまとめたもの。
    """
    ok = await save_user_growth_note(note)
    if not ok:
        return "記録に失敗しました。"
    return f"「{note}」、覚えておくね。"


@tool
async def get_user_growth_notes(query: str = "") -> str:
    """まがときさんから「前に言ってた成長の話、覚えてる？」「最近自分が成長したことって何だっけ」
    のように、過去に自分が語った成長・自慢の記録について尋ねられたときに呼ぶツールです。

    Args:
        query: 特定の話題について聞かれた場合はそのキーワードを入れてください。
               漠然とした「最近どう？」的な質問には空のまま呼んでください。
    """
    notes = await get_recent_growth_notes(limit=10)
    if query.strip():
        notes = [n for n in notes if query.strip() in n.get("note", "")]
    if not notes:
        return "まだ記録されている成長の話はありません。"
    lines = [f"- {n.get('note', '')}" for n in notes[:5]]
    return "覚えている成長の話:\n" + "\n".join(lines)
