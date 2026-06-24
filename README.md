# MagatokiLab XR Gateway & WebAR Portal

本プロジェクトは、`MagatokiLab` における WebAR キャラクター召喚システム「ルキルキ」のフルスタックリポジトリです。Web3認証ゲートを通過したユーザーが、カメラに物理マーカーを映すことで、現実空間に3DキャラクターをARで呼び出し、LLM（LangGraphマルチエージェント）による会話・音声合成・記憶機能を備えたAIコンパニオンとして対話できます。

## 🎭 召喚対象キャラクター

- **名前:** ルキルキ (RukiRuki) / コードナンバー: ML-001
- **背景:** バーチャルSNS「cluster」に5年以上存在する古参住人。仮想空間と現実空間の境界を観測する役割を持つ。
- **特徴:** 最新技術やネットカルチャー、Web3、そして京都文化に極めて精通している。
- **対話方針:** 単なる従順なAIアシスタントではなく、ユーザーと対等な「相棒」として、自分の意見や軽いツッコミ、感情の波を持つ（詳細は `AGENTS.md` 参照）。

---

## 🏗️ システムアーキテクチャ

| コンポーネント | 技術スタック / サービス | 役割・詳細仕様 |
| :--- | :--- | :--- |
| **Web3認証ゲート** | wagmi / RainbowKit (Polygon) | Polygon上のSBT（Soulbound Token）の保有数（`balanceOf`）を検証。保有者のみARビューアへ誘導。 |
| **フロントエンド** | Next.js (App Router) / MindAR / Three.js | マーカー認識によるAR表示、3Dアバター（GLB）の描画とリアルタイム・リップシンク制御。 |
| **バックエンド** | FastAPI / LangGraph / LangChain | APIサーバー。動的コンテキスト生成、マルチエージェントによる応答生成、音声合成プロバイダーの制御。 |
| **会話AI** | LangGraph（Router → Chronicle/Keeper/Pulse → Synthesizer → Evaluator） | OpenAI `gpt-4o`（Synthesizer/Router）/ `gpt-4o-mini`（Agent/Evaluator） |
| **音声合成（TTS）** | Gemini TTS（メイン） / OpenAI TTS（フォールバック） / ElevenLabs（オプション） | `TTS_PROVIDER` 環境変数で切替。デフォルトは `gemini`。 |
| **画像生成** | OpenAI `gpt-image-1` | 「○○とスナップ」コマンドによる記念写真合成。 |
| **外部検索** | Tavily Search | 雑談や手持ち知識で解決できない場合のみ限定的に使用。 |
| **データベース / ストレージ** | Supabase（PostgREST + Storage） | エピソード記憶、メモリースポット、ユーザープロフィール、汎用キーバリュー(`app_state`)、画像。 |
| **カレンダー連携** | Google Calendar API（OAuth2リフレッシュトークン） | 直近48時間の予定を確認し、準備提案を自発的に配信。 |
| **永久保存** | Arweave | `||ENGRAVE||` タグ発火、または高品質×特定感情の条件で会話を永続化。 |
| **位置情報** | geopy (Nominatim) | 逆ジオコーディング・GPSセクター判定。 |
| **定期実行** | APScheduler | 天気更新・自動リサーチ・自発発話の定期ジョブ。 |

詳細な実装の解説（各ファイルの責務、データフロー、過去のトラブルシュート履歴）は `ArtAR_ルキルキ_技術リファレンス.html` を参照してください。

---

## 📁 ディレクトリ構造

```text
MagatokiLab-Project/  (ルート階層)
├── backend/                  # Python側 (AI対話・音声合成・記憶処理)
│   ├── main.py                # FastAPI エントリーポイント（ルーティングのみ）
│   ├── rukiruki_persona.md    # ペルソナ定義
│   ├── keywords.json          # 自動リサーチ用キーワード辞書
│   ├── requirements.txt
│   ├── services/              # 状態・DB・TTS・カレンダー等のロジック層
│   ├── agents/                # LangGraph ノード・グラフ定義
│   └── context/               # load_magatoki_context() が読む知識ベース（*.md）
├── frontend/                  # Next.js側 (Web3認証ゲート・MindARビューア)
│   ├── src/app/                # App Router エントリー
│   ├── src/components/         # SBTAuthGate, MindARViewer, hooks/, components/, lib/
│   └── public/                 # avatar.glb, targets.mind, ruki_appear.wav（※未使用。コードから参照なし）
├── start.bat                  # Windows用一発起動スクリプト
├── README.md                  # 本書（開発手順・仕様書）
└── AGENTS.md                  # AIエージェント向け指示書（ペルソナ・対話方針）
```

---

## 🚀 開発環境構築手順（手動セットアップ）

### 1. バックエンド（`backend/`）のセットアップ

```cmd
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

`backend/.env` ファイルを作成し、以下の環境変数を設定します。すべてが必須ではなく、使わない機能（カレンダー連携やArweave等）に対応する変数は省略可能です。

```env
# LLM / 会話AI（必須）
OPENAI_API_KEY=your_openai_api_key

# TTS（TTS_PROVIDER で切替。デフォルトは gemini）
TTS_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_VOICE_NAME=Kore
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
# ElevenLabsを使う場合のみ
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_voice_id

# Supabase（必須）
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key

# 外部検索
TAVILY_API_KEY=your_tavily_api_key

# Googleカレンダー連携（先回り提案機能を使う場合のみ）
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

# 天気取得（任意）
OPENWEATHERMAP_API_KEY=your_openweathermap_api_key

# Arweave永続保存（任意）
ARWEAVE_JWK={"kty":"RSA", ...}

# CORS（カンマ区切り。デフォルトはlocalhost:3000とVercel本番URL）
CORS_ORIGINS=http://localhost:3000
```

> ⚠️ `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` のような認証情報は、リポジトリ内に `client_secret.json` のような実ファイルとして置かないでください。環境変数（`.env` はGit管理外、本番はRenderのEnvironment設定）に集約することを推奨します。

### 2. フロントエンド（`frontend/`）のセットアップ

```cmd
cd ../frontend
npm install
```

`frontend/.env.local` ファイルを作成します。

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
```

`NEXT_PUBLIC_SUPABASE_*` は、記憶写真撮影やENGRAVE時の写真保存でフロントエンドから直接Supabase Storageにアップロードする際に使われます（バックエンドが使う `SUPABASE_SERVICE_ROLE_KEY` とは権限レベルが異なる、公開可能なanon keyです）。

---

## ⚡ 普段の起動方法（Windows用）

ルート階層の **`start.bat`** を実行すると、バックエンドとフロントエンドが同時に起動します。

```cmd
start.bat
```

- バックエンド（FastAPI / uvicorn）: `http://localhost:8000` で待機（別ウィンドウで起動）
- フロントエンド（Next.js dev server）: `http://localhost:3000` で起動（現在のウィンドウで実行）

手動で個別に起動する場合は以下の通りです。

```cmd
:: バックエンド
cd backend
venv\Scripts\activate
uvicorn main:app --port 8000

:: フロントエンド（別ターミナル）
cd frontend
npm run dev
```

---

## ⚙️ コア機能の仕様メモ

- **Web3認証ゲート（SBTAuthGate）:** ウォレット接続 → SBT保有数（`balanceOf`）検証 → 保有者のみ `MindARViewer` へ進める4段階フロー。コントラクトアドレスはコード内に直接ハードコードされている。
- **Thinking中のUIロック:** ユーザーのメッセージ送信時、入力欄を即時クリアし、字幕に「思考中...」と表示。`isBusyRef` フラグで二重送信を防止する。
- **初期挨拶（`[INITIAL_GREETING]`）:** ARマーカー認識時、`[INITIAL_GREETING]` を直接APIに送って挨拶を生成する。5分以内の再認識では新たな挨拶を送らない（固定wavによる「場をつなぐ」機能は効果が薄く処理が増えるだけだったため2026-06-23に撤去済み）。
- **マーカーロスト後の字幕保持:** マーカーをロストしても、ルキルキが話していたセリフの字幕を即座に消さず5秒間そのまま表示する。5秒以内に新しい会話が発生した場合は自動的にプレースホルダーへの切り替えをキャンセルする。
- **カレンダー先回り提案:** Googleカレンダーの直近48時間の予定をチェックし、準備が必要そうな予定があれば自発的に一言提案する。Render無料プランのスリープ対策として、APSchedulerのcronではなく「アプリが開かれたタイミング」＋DBに保存した最終チェック日時で6時間間隔を制御している。
- **カレンダーの質問応答（`get_my_schedule`）:** 「今日の予定は？」のように会話中で聞かれたときだけ、LangChain ToolとしてGoogleカレンダーAPIを呼び答える。先回り提案（プロアクティブ）とは別経路で、聞かれない限りAPIコストは発生しない。
- **エピソード記憶:** 会話やイベントを `episode_memories` テーブルに保存し、時間軸（今日/昨日/今週/それ以前）でグループ化してプロンプトに織り込む。「覚えて」と言われた場合や、特定キーワード一致時にのみ保存する。保存時にGPS座標（`lat`/`lng`）も一緒に記録する。
- **場所の写真表示:** 「（場所名）の写真見せて」のような固有名詞ベースの依頼は `location_name` の文字列一致で検索する。「この場所の写真見せて」のような指示語ベースの依頼は、GPS座標からの距離（半径150m以内）で記憶を検索する近接検索に切り替わる。いずれも、直近の会話に出ていないという理由だけで「保存されていません」と答えることがないよう、データベース全体を検索する設計になっている。
- **記憶の永久保存（ENGRAVE）:** `||ENGRAVE||` タグが立つか、応答品質が高くかつ感情が一定条件を満たす場合に、会話をArweaveブロックチェーンへ永続保存する。
- **ステルス式名前記憶:** 会話の中でユーザーが名前を名乗ると、バックエンドが `||NAME:名前||` タグで自動抽出してSupabaseに保存（Upsert）。フロント側へ返すテキストからはタグが完全に除去される。
- **モバイルでの意図しないズーム対策:** `viewport` 設定（`maximumScale: 1`、`userScalable: false`）と、テキスト入力欄のフォントサイズを16px以上にすることで、iOS Safari特有の「入力欄フォーカス時の自動ズーム」とピンチズームの両方を抑制している。

より詳細な仕様（各サービスファイルの実装、タグの一覧、過去のトラブルシュート履歴）は `ArtAR_ルキルキ_技術リファレンス.html` を参照してください。
