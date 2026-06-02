@echo off
chcp 65001 > nul
echo 🚀 MagatokiLab ローカル開発環境を起動します...

:: 1. バックエンドの起動（新しいコマンドプロンプト画面を裏で開いて実行します）
echo 📦 バックエンド (FastAPI) を起動中...
start "MagatokiLab - Backend" cmd /k "cd backend && venv\Scripts\activate && uvicorn main:app --port 8000"

:: 2. フロントエンドの起動（現在のコマンドプロンプト画面でそのまま実行します）
echo 💻 フロントエンド (Next.js) を起動中...
cd frontend
npm run dev