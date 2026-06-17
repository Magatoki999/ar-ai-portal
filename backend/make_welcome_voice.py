import os
import asyncio
import base64
from dotenv import load_dotenv

# .env から APIキーやプロバイダー設定をロード
load_dotenv()

# tts.py の配置場所（services/tts.py）から統合関数をインポート
from services.tts import generate_tts

async def main():
    # 🧠 【ペルソナ反映】感嘆詞、教授への呼びかけ、口調の崩しを完全インストール！
    text = "あ、まがときさん！ルキルキ、同期完了ですよっ！さあ、始めるよ！"
    
    provider = os.getenv("TTS_PROVIDER", "gemini").lower()
    
    print(f"🎙️ 現在の設定プロバイダー [{provider}] でルキルキの音声（ペルソナ反映版）を生成中...")
    print(f"💬 セリフ: 「{text}」")
    
    # 統合ディスパッチャーを呼び出し（base64文字列が返る）
    audio_b64 = await generate_tts(text)
    
    if not audio_b64:
        print("❌ ERROR: 音声合成に失敗しました。.env の各種APIキーや環境変数を確認してください。")
        return
    
    # base64データをバイナリにデコード
    audio_bytes = base64.b64decode(audio_b64)
    
    # プロバイダーに合わせて拡張子を決定
    if provider == "gemini":
        filename = "welcome_ruki.wav"
    else:
        filename = "welcome_ruki.mp3"
        
    # 📂 【利便性強化】プロジェクトのルートにある Next.js の public/ フォルダの絶対パスを計算
    base_dir = os.path.dirname(os.path.abspath(__file__))  # backend/ フォルダ
    project_root = os.path.dirname(base_dir)              # ar-ai-portal/ ルートフォルダ
    public_dir = os.path.join(project_root, "public")       # public/ フォルダ
    
    # public フォルダが存在するかチェック（なければ backend 直下に保存）
    if os.path.exists(public_dir):
        output_path = os.path.join(public_dir, filename)
        dest_name = f"Next.jsの public/{filename}"
    else:
        output_path = filename
        dest_name = f"ローカルの {filename}"
        
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
        
    print(f"✨ 成功しました！ 『{dest_name}』 として直接保存完了しました、教授！")

if __name__ == "__main__":
    asyncio.run(main())