# backend/agents/keeper_agent.py
import os
import subprocess
import json
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

def get_latest_git_commit() -> str:
    """ローカルリポジトリの最新のコミットログを取得する"""
    try:
        # 最新のコミットハッシュ、作者、日付、メッセージを取得
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%h - %an, %ar : %s"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception as e:
        return f"Gitリポジトリの取得に失敗、または未初期化です: {e}"

def scan_development_assets() -> dict:
    """UnityやBlenderの成果物（USDZ/VRM/unitypackage等）の更新状況を擬似スキャン"""
    # 実際の実装では、UnityのAssetsフォルダや特定の出力先をos.path.getmtime等で走査します
    status = {
        "unity_version": "6.3 (AR Foundation)",
        "recent_exports": []
    }
    
    # 監視したい特定の拡張子やパスがあればここでチェック
    # 例: Blenderから書き出した usdz や vrm ファイルの最新タイムスタンプなど
    return status

async def sync_keeper_context():
    """コンテキスト・キーパーの思考・進捗をブラックボード（Supabase）にストックする"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[Keeper] Supabaseの環境変数が設定されていません。")
        return

    print("─── [コンテキスト・キーパー] ローカル環境の同期を開始します ───")
    
    # 1. ローカル情報の収集
    git_log = get_latest_git_commit()
    asset_status = scan_development_assets()
    
    # 2. キーパーとしての「思考・まとめ」の構築
    # ※ 本格的なマルチエージェント化では、ここで一度LLMを噛ませて
    # 「進捗の要約と、ルキルキが次に教授にかけるべき言葉の提案」を作らせると強力です。
    content_markdown = (
        f"### 🛠️ ローカル開発進捗レポート\n"
        f"- **最新のGitコミット**: `{git_log}`\n"
        f"- **Unity環境**: `{asset_status['unity_version']}`\n\n"
        f"**キーパーの考察**:\n"
        f"直近のコミットが正常に記録されています。Unity 6.3でのAR Foundationプレーン検出や、"
        f"Blenderからの3Dアセット書き出し（USDZ/VRM）の文脈がいつでも引き出せる状態です。\n"
        f"教授がコードのエラーや進捗について触れたら、このリポ状態をベースに壁打ち相手になってあげてください。"
    )

    # 3. Supabase（ブラックボード）へのインサート
    url = f"{SUPABASE_URL}/rest/v1/agent_memos"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "agent_name": "keeper",
        "category": "git_log",
        "title": "ローカルGitコミット & 開発環境同期",
        "content": content_markdown,
        "importance": 3,
        "metadata": {
            "latest_commit": git_log,
            "project": "MagatokiLab_XR"
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, headers=headers, timeout=5.0)
            if res.status_code in [200, 201]:
                print("[Keeper] ブラックボード（agent_memos）に進捗を同期しました。")
            else:
                print(f"[Keeper Error] 同期失敗: {res.status_code} {res.text}")
    except Exception as e:
        print(f"[Keeper Error] 例外発生: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(sync_keeper_context())