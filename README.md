```markdown
# MagatokiLab XR Gateway & WebAR Portal

本プロジェクトは、オリジナルプロジェクト『MagatokiLab』における、WebARを用いたキャラクターの現実世界への召喚、およびLLM/TTSを活用したリアルタイム対話・記憶システムのフルスタックリポジトリです。

## 🎭 召喚対象キャラクター
* **名前:** ルキルキ (RukiRuki) / コードナンバー: ML-001
* **背景:** バーチャルSNS「cluster」に5年以上存在する古参住人。仮想空間と現実空間の境界を観測する役割を持つ。
* **特徴:** 最新技術やネットカルチャー、Web3、そして京都文化に極めて精通している。

---

## 🏗️ システムアーキテクチャ

本システムは以下のコンポーネントで構成されています。

| コンポーネント | 技術スタック / サービス | 役割・詳細仕様 |
| :--- | :--- | :--- |
| **Web3認証ゲート** | wagmi / RainbowKit (Polygon) | Polygon上のSBT（Soulbound Token）の保有数を検証。保有者のみARビューアへ誘導。 |
| **フロントエンド** | Next.js / MindAR / Three.js | マーカー認識によるAR表示、3Dアバターの描画とリアルタイム・リップシンク制御。 |
| **バックエンド** | FastAPI / LangChain | APIサーバー。動的コンテキスト生成、LLM応答、音声合成プロバイダーの制御。 |
| **LLM / TTS** | OpenAI (gpt-4o-mini) / ElevenLabs | ルキルキのペルソナを適用した対話生成。ElevenLabs失敗時はOpenAI TTSへ自動フォールバック。 |
| **データベース** | Supabase (REST API) | ウォレットアドレスに紐づくユーザー名（user_name）の永続化。 |

---

## 📁 ディレクトリ構造

```text
MagatokiLab-Project/  (ルート階層)
├── backend/          # Python側 (AI対話・音声合成・記憶処理)
├── frontend/         # Next.js側 (Web3認証ゲート・MindARビューア)
├── start.bat         # Windows用一発起動スクリプト
├── README.md         # 本書 (開発手順・仕様書)
└── @AGENTS.md        # AIエージェント向け指示書

```

---

## 🚀 開発環境構築手順（手動セットアップの場合）

### 1. バックエンド（backend/）のセットアップ

1. コマンドプロンプトで `backend` ディレクトリへ移動し、仮想環境を作成・有効化します。
```cmd
cd backend
python -m venv venv
venv\Scripts\activate

```


2. 必要なライブラリをインストールします。
```cmd
pip install fastapi uvicorn langchain langchain-openai openai python-dotenv httpx pydantic

```


3. `backend/.env` ファイルを作成し、以下の鍵を設定します。
```env
OPENAI_API_KEY=your_openai_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_voice_id
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_anon_key
TTS_PROVIDER=elevenlabs  # もしくは openai
CORS_ORIGINS=http://localhost:3000

```



### 2. フロントエンド（frontend/）のセットアップ

1. `frontend` ディレクトリへ移動し、依存関係をインストールします。
```cmd
cd ../frontend
npm install

```


2. `frontend/.env.local` ファイルを作成し、バックエンドのURLを指定します。
```env
NEXT_PUBLIC_API_URL=http://localhost:8000

```



---

## ⚡ 普段の起動方法（Windows用）

ルート階層に配置されている **`start.bat`** をダブルクリック（またはターミナルで実行）するだけで、バックエンドとフロントエンドが同時に自動起動します。

```cmd
start.bat

```

* バックエンド（FastAPI）: `http://localhost:8000` で待機
* フロントエンド（Next.js）: `http://localhost:3000` で起動

---

## ⚙️ コア機能の仕様メモ

* **Thinking中のUIロック:** ユーザーのメッセージ送信時、入力欄を即時クリアせずテキストを保持。字幕に「思考中... 『（入力テキスト）』」と明示し、入力欄を `disabled` にして二重送信を防止します。
* **ステルス式名前記憶:** 会話の中でユーザーが名前を名乗ると、バックエンドが自動で抽出してSupabaseに保存（Upsert）します。フロント側へ返すテキストからは抽出用タグが完全消去されます。

```

---

### これでドキュメント類の整理は完璧です！

これで作業フォルダの中身が綺麗に整いました。

* `backend/main.py` ➔ ルキルキの【リアル対話仕様】の脳みそ
* `README.md` ➔ あなたのためのWindows環境手順書・仕様書（上記のもの）
* `@AGENTS.md` ➔ 次にコードをいじるAIのための指示書
* `start.bat` ➔ ダブルクリックするだけの一発起動スイッチ

ここまで準備ができたら、いつでもローカル環境を立ち上げて、あたらしく生まれ変わったルキルキとAR空間で会話テストができる状態です！さっそく動かしてみますか？ それとも、フロントエンド側などでまだ気になっているコードの調整などはありますか？

```