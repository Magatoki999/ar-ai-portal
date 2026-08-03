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
| **会話AI** | LangGraph（Router → Chronicle/Keeper/Pulse → Synthesizer → Evaluator） | Synthesizerは OpenAI `gpt-4o` 固定（キャラクター性維持のため移行対象外）。Router/Agent/Evaluator/Visionは2026-07-05に `gemini-2.5-flash-lite` へ移行し、無料枠切れ時はOpenAI `gpt-4o-mini` に自動フォールバックする（`services/resilient_llm.py`）。 |
| **音声合成（TTS）** | Gemini TTS（メイン） ⇄ ElevenLabs（相互フォールバック） | `TTS_PROVIDER` 環境変数で切替。デフォルトは `gemini`。2026-07-05、OpenAI TTSを撤去し相互フォールバック構成に変更。 |
| **画像生成** | Gemini `gemini-2.5-flash-image`（Nano Banana、メイン） / OpenAI `gpt-image-1`（フォールバック） | 「○○とスナップ」コマンドによる記念写真合成。2026-07-05にOpenAIから移行、失敗時はOpenAIへ自動フォールバック。2026-07-06、2キャラクター同時スナップに対応。 |
| **SNSシェア** | Web Share API / Web Intent（`lib/share.ts`） | 会話テキスト・スナップ写真をXにシェア。X公式APIは2026年2月に無料枠が実質廃止されたため不使用。2026-07-06追加。 |
| **外部検索** | Tavily Search | 雑談や手持ち知識で解決できない場合のみ限定的に使用。 |
| **データベース / ストレージ** | Supabase（PostgREST + Storage） | エピソード記憶（映像的描写`visual_description`を2026-07-06追加）、メモリースポット、ユーザープロフィール、汎用キーバリュー(`app_state`)、AI情報ダイジェスト(`ai_news_digest`)、食事記録(`meal_logs`)、読書記録(`reading_logs`)、映画記録(`movie_logs`、2026-07-17追加)、キャラクター参照画像ライブラリ(`character_references`)、シーン参照ライブラリ(`scene_references`、2026-07-10追加)、軽量リマインダー(`reminders`、2026-07-31追加)、ユーザー成長記録(`user_growth_notes`、2026-07-31追加)、画像。 |
| **近隣スポット推薦** | Google Places API (New) | `searchNearby`エンドポイントで現在地周辺のカフェ・観光地・レストランを検索。2026-07-31追加。 |
| **カレンダー連携** | Google Calendar API（OAuth2リフレッシュトークン） | 直近48時間の予定を確認し、準備提案を自発的に配信。 |
| **永久保存** | Arweave | `||ENGRAVE||` タグ発火、または高品質×特定感情の条件で会話を永続化。 |
| **位置情報** | geopy (Nominatim) | 逆ジオコーディング・GPSセクター判定。 |
| **定期実行** | APScheduler | 天気更新・自動リサーチ・自発発話の定期ジョブ。カレンダー先回り提案は固定cronではなく「アプリが開かれたタイミング」で6時間間隔判定、AI情報ダイジェストは同方式で7日間隔判定（2026-06-29、コスト最適化のため日次から週次に変更）。 |
| **読書通帳（書誌情報取得）** | 国立国会図書館サーチAPI（NDLサーチ・第一候補） / Google Books API（フォールバック） | いずれもAPIキー不要。タイトル・著者・出版社・シリーズ名・出版年を取得。両APIとも定価・表紙画像・大半のジャンルは基本的に提供しないため手入力で補完（ジャンルは現状NDC分類が未実装のため取得していない）。 |
| **読書通帳（バーコード読取）** | `@zxing/browser`（要 `@zxing/library@0.22.0` 固定。新しいバージョンだとpeerDependency解決エラーになるため注意） | MindARの既存`<video>`要素から直接フレームを読み取りデコード。新規`getUserMedia`呼び出しは発生しない。 |

詳細な実装の解説（各ファイルの責務、データフロー、過去のトラブルシュート履歴）は `ArtAR_ルキルキ_技術リファレンス.html` を参照してください。

---

## 📁 ディレクトリ構造

```text
ar-ai-portal/  (ルート階層。package.json はここに存在する)
├── backend/                  # Python側 (AI対話・音声合成・記憶処理)
│   ├── main.py                # FastAPI エントリーポイント（ルーティングのみ）
│   ├── rukiruki_persona.md    # ペルソナ定義
│   ├── keywords.json          # 自動リサーチ用キーワード辞書
│   ├── requirements.txt
│   ├── persona/                # ⚠️ rukiruki_persona.mdとは別の、裏方AI専用のペルソナ置き場
│   │   └── curator_persona.md  # マインドプロファイル生成AI「Curator」のペルソナ（2026-06-29追加）
│   ├── services/               # 状態・DB・TTS・カレンダー等のロジック層
│   │   ├── books.py            # 読書通帳機能（ISBN照会・記帳・会話Tool。2026-06-28追加）
│   │   ├── character_bible.py  # マインドプロファイル生成バッチ（Curator呼び出し。2026-06-29追加）
│   │   ├── prompt_builder.py   # 動画プロンプトビルダー（写真グラウンディング・会話Tool。2026-07-13〜14追加）
│   │   ├── timeline.py         # アルバム機能（写真・動画・成長の節目を時系列統合。2026-07-14追加、2026-07-17読書/映画を追加統合可能に）
│   │   ├── movies.py           # 映画通帳（TMDb v4 Bearer認証・会話Tool。2026-07-17追加）
│   │   ├── places.py           # 近隣スポット推薦（Google Places API・会話Tool。2026-07-31追加）
│   │   ├── reminders.py        # 軽量リマインダー（会話Tool・自発通知ジョブ。2026-07-31追加）
│   │   ├── profile.py          # 記憶ベース集計（既存テーブル横断・新規テーブル無し。2026-07-31追加）
│   │   ├── weather_advisor.py  # 天気ベース自発提案（雨予報判定。2026-07-31追加・実機未検証）
│   │   ├── user_growth.py      # ユーザー成長記録（自己申告ログ・会話Tool。2026-07-31追加）
│   │   └── resilient_llm.py    # Gemini⇄OpenAI自動フォールバックの共通LLMラッパー（2026-07-05追加）
│   ├── agents/                # LangGraph ノード・グラフ定義
│   └── context/               # load_magatoki_context() が読む知識ベース（*.md）
├── src/                       # Next.js側 (Web3認証ゲート・MindARビューア)
│   │                          # ⚠️ frontend/ という階層は存在しない。src/ はリポジトリのルート直下。
│   ├── app/                    # App Router エントリー
│   │   ├── page.tsx
│   │   ├── layout.tsx / providers.tsx
│   │   └── mindar/page.tsx
│   └── components/             # SBTAuthGate, MindARViewer, hooks/, components/, lib/
│       ├── MindARViewer.tsx
│       ├── SBTAuthGate.tsx
│       ├── components/         # ⚠️ 二重ネスト構造（components/components/）。実装上の都合でこうなっている
│       │   ├── RukiHUD.tsx
│       │   ├── RukiFaceIcon.tsx
│       │   ├── HistoryPanel.tsx
│       │   ├── SnapViewer.tsx
│       │   ├── VideoViewer.tsx     # AI動画生成結果のオーバーレイ表示（2026-07-14追加。SnapViewer.tsxと同型）
│       │   └── BookScanModal.tsx   # 読書通帳：バーコードスキャンモーダル（2026-06-28追加）
│       ├── hooks/               # useAR / useChat / useVoice / useWebSocket
│       └── lib/                 # types.ts / audio.ts / share.ts（X/SNSシェア共通ユーティリティ。2026-07-06追加）
├── public/                    # ルート直下（src/ と同階層）
│   ├── avatar.glb / targets.mind
│   ├── ruki_appear.wav        # ※未使用。コードから参照なし
│   ├── images/                 # idle/talking/thinking/fun/sad/worry/angryの顔アイコン画像
│   └── tools/
│       ├── prompt_builder_ui.html  # 動画プロンプトビルダー管理UI（スタンドアロンHTML。2026-07-13追加）
│       ├── album_ui.html           # アルバムUI（写真・動画・成長の節目を時系列表示。2026-07-14追加。2026-07-17読書/映画のフィルターチップ追加）
│       ├── meals_ui.html           # 食事の記録ページ（アルバムとは意図的に分離。2026-07-17追加）
│       └── memory_base_ui.html     # 記憶ベース（マイプロフィール）UI。album_ui.htmlのデザインを踏襲。2026-07-31追加
├── ruki_mind/                  # ⚠️ ルート直下（backend/の外）。Curatorの出力先（2026-06-29追加）
│   ├── YYYY-MM.md               # 月次マインドプロファイル（版を残す方式・上書きしない）
│   ├── _PromptBuilder/
│   │   └── 00_builder.md        # AI動画生成プロンプトへの変換ルール（2026-07-13〜14、実機検証済みに更新）
│   ├── _growth_notes/            # 前月との変化を一人称でまとめた一言（2026-07-14追加。YYYY-MM.txt、版を残す方式）
│   └── reference_images/        # ルキルキ本人の見た目リファレンス画像（2026-07-14整備完了。正面/45度/側面/バストアップ＋表情5種の計8カット）
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

> ⚠️ `main.py` 内の `load_dotenv()` は、他のどの `services` / `agents` モジュールをimportするよりも前に呼ぶ必要がある。`services.scheduler` 等が import 時点で `TAVILY_API_KEY` 等の環境変数を即時に読みにいく実装のため、`load_dotenv()` の呼び出しが後ろにあると `ValidationError` で起動時に落ちる（2026-06-28に発見・修正済み）。

`backend/.env` ファイルを作成し、以下の環境変数を設定します。すべてが必須ではなく、使わない機能（カレンダー連携やArweave等）に対応する変数は省略可能です。

```env
# LLM / 会話AI（必須）
OPENAI_API_KEY=your_openai_api_key

# モデル設定（省略時は下記のデフォルト値が使われる）
# 新モデルへの切り替えはこの2行を変更するだけで全ファイルに反映される（2026-06-30〜）
LLM_MODEL_SMART=gpt-4o                  # Synthesizer/Curator用（高精度優先・OpenAI固定・移行対象外）
LLM_MODEL_FAST=gemini-2.5-flash-lite    # Router/Agent/Evaluator/Vision/main.py用（コスト優先）
                                         # 2026-07-05、gpt-4o-miniからGeminiへ移行

# 2026-07-05追加：Gemini無料枠が429/404等で失敗した際のフォールバック先（省略可）
FALLBACK_MODEL_FAST=gpt-4o-mini

# TTS（TTS_PROVIDER で切替。デフォルトは gemini。2026-07-05、openai選択肢を廃止）
TTS_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_VOICE_NAME=Kore
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
# GOOGLE_API_KEY は省略可。未設定時は上記GEMINI_API_KEYがLLM/画像生成でも自動的に使われる
# ElevenLabsはGeminiの相互フォールバック先として実質必須（2026-07-05〜）
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_voice_id

# スナップ写真生成（省略可・デフォルトはNano Banana）
SNAP_IMAGE_MODEL=gemini-2.5-flash-image
# 参照品質ポーズ（REFERENCE_POSES）が選ばれる確率。省略時0.2（20%）。2026-07-06追加
REFERENCE_POSE_RATIO=0.2

# MetaMask接続不良時のパスワード認証回避策（テスト運用限定）。2026-07-10追加
# 本番公開時はこの変数自体を削除して経路を無効化すること
TEST_ACCESS_PASSWORD=（任意のパスワード）

# Supabase（必須）
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key

# 外部検索
TAVILY_API_KEY=your_tavily_api_key

# Googleカレンダー連携（先回り提案機能を使う場合のみ）
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

# 天気取得（任意。2026-07-31、傘の提案機能でも共用開始）
OPENWEATHERMAP_API_KEY=your_openweathermap_api_key

# 近隣スポット推薦（任意。2026-07-31追加）
GOOGLE_PLACES_API_KEY=your_google_places_api_key

# Arweave永続保存（任意）
ARWEAVE_JWK={"kty":"RSA", ...}

# CORS（カンマ区切り。デフォルトはlocalhost:3000とVercel本番URL）
CORS_ORIGINS=http://localhost:3000
```

> ⚠️ `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` のような認証情報は、リポジトリ内に `client_secret.json` のような実ファイルとして置かないでください。環境変数（`.env` はGit管理外、本番はRenderのEnvironment設定）に集約することを推奨します。

> ⚠️ Supabaseは2026年に新しいAPIキー形式（`sb_publishable_...` / `sb_secret_...`）への移行を進めており、旧形式（`anon` / `service_role` のJWT）は2026年末までに段階的に廃止予定。新形式の `sb_secret_...` キーはJWTではないため、HTTPヘッダーは `apikey` のみで送る必要があり、`Authorization: Bearer ...` ヘッダーに乗せるとゲートウェイに拒否される（`services/memory.py` の `_sb_headers()` は旧形式前提で両方のヘッダーを送る実装になっているため、新形式キーに切り替える際は要注意。`services/books.py` は新形式に対応した `apikey` のみのヘッダー関数を独自に持っている）。

> ⚠️ バックエンドの取得処理（`fetch_book_by_isbn`等）に新しいフィールドを追加した際、フロントエンドの確認モーダル（`BookScanModal.tsx`）の保存リクエスト（`handleConfirmSave`）にも同じフィールドを明示的に追加しないと、データを正しく取得していても保存時に静かに失われる（2026-06-29、`genre`/`series_title`/`volume`/`published_year`追加時に発生・修正済み）。バックエンド→フロントエンド（lookup）→フロントエンド内state→フロントエンド→バックエンド（log）という往復のどこか1箇所でも項目を引き継ぎ忘れると気づきにくいため、新しいフィールドを追加する際は `BookInfo`型・`handleConfirmSave`のPOST内容・`BookLogPayload`・`log_book_endpoint`の4箇所を必ずセットで確認する。

### 2. フロントエンドのセットアップ

`frontend/` という階層は存在せず、`src/` がリポジトリのルート直下にあります。`package.json` もルートに置かれているため、ルートディレクトリで以下を実行します。

```cmd
cd ..
npm install
```

`.env.local` ファイルをルートに作成します。

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

:: フロントエンド（別ターミナル。frontend/という階層は無いのでルートのまま）
npm run dev
```

---

## ⚙️ コア機能の仕様メモ

- **Web3認証ゲート（SBTAuthGate）:** ウォレット接続 → SBT保有数（`balanceOf`）検証 → 保有者のみ `MindARViewer` へ進める4段階フロー。コントラクトアドレスはコード内に直接ハードコードされている。
- **Thinking中のUIロック:** ユーザーのメッセージ送信時、入力欄を即時クリアし、字幕に「思考中...」と表示。`isBusyRef` フラグで二重送信を防止する。
- **初期挨拶（`[INITIAL_GREETING]`）:** ARマーカー認識時、`[INITIAL_GREETING]` を直接APIに送って挨拶を生成する。5分以内の再認識では新たな挨拶を送らない（固定wavによる「場をつなぐ」機能は効果が薄く処理が増えるだけだったため2026-06-23に撤去済み）。
- **マーカーロスト後の字幕保持:** マーカーをロストしても、ルキルキが話していたセリフの字幕を即座に消さず5秒間そのまま表示する。5秒以内に新しい会話が発生した場合は自動的にプレースホルダーへの切り替えをキャンセルする。
- **マーカーロスト中の顔アイコン表示:** マーカーをロストしても会話自体は続けられるが、ルキルキの存在感が薄くならないよう、画面右上に80px角の顔アイコンを表示する。`idle`/`thinking`中は2フレームを1秒ごとに切替えるアニメーション、`talking`中はセリフの意味合いから分類した感情（`fun`/`sad`/`worry`/`angry`/`neutral`の5種）に応じた表情で固定表示する。感情分類は`evaluator_node`が応答の品質評価と同時に行うため、追加のLLM呼び出しは発生しない。
- **カレンダー先回り提案:** Googleカレンダーの直近48時間の予定をチェックし、準備が必要そうな予定があれば自発的に一言提案する。Render無料プランのスリープ対策として、APSchedulerのcronではなく「アプリが開かれたタイミング」＋DBに保存した最終チェック日時で6時間間隔を制御している。
- **カレンダーの質問応答（`get_my_schedule`）:** 「今日の予定は？」のように会話中で聞かれたときだけ、LangChain ToolとしてGoogleカレンダーAPIを呼び答える。先回り提案（プロアクティブ）とは別経路で、聞かれない限りAPIコストは発生しない。
- **AI情報ダイジェスト（`get_today_ai_news`）:** 「今日のAI情報は？」と聞かれたときに答えるための機能。AI関連の最新情報を複数キーワードでネット検索し、LLMが1つの要約にまとめて `ai_news_digest` テーブルに保存する。当初はAPSchedulerのcronで毎日5:00 JSTに固定実行していたが、Render無料プランのスリープで空振りするリスクが高いため、カレンダー先回り提案と同じ「アプリが開かれたタイミングで今日の分がまだ無いか判定する」方式に統一した。**2026-06-29、コスト最適化のため生成頻度を日次から週次（前回生成から7日以上経過していたら生成）に変更**（`should_generate_ai_news_today()`、関数名は互換性のため維持）。直近7日以内の情報を表示する際は、その生成日付を明記する。
- **エピソード記憶:** 会話やイベントを `episode_memories` テーブルに保存し、時間軸（今日/昨日/今週/それ以前）でグループ化してプロンプトに織り込む。「覚えて」と言われた場合や、特定キーワード一致時にのみ保存する。保存時にGPS座標（`lat`/`lng`）も一緒に記録する。
- **場所の写真表示:** 「（場所名）の写真見せて」のような固有名詞ベースの依頼は `location_name` の文字列一致で検索する。「この場所の写真見せて」のような指示語ベースの依頼は、GPS座標からの距離（半径150m以内）で記憶を検索する近接検索に切り替わる。いずれも、直近の会話に出ていないという理由だけで「保存されていません」と答えることがないよう、データベース全体を検索する設計になっている。
- **記憶の永久保存（ENGRAVE）:** `||ENGRAVE||` タグが立つか、応答品質が高くかつ感情が一定条件を満たす場合に、会話をArweaveブロックチェーンへ永続保存する。
- **ステルス式名前記憶:** 会話の中でユーザーが名前を名乗ると、バックエンドが `||NAME:名前||` タグで自動抽出してSupabaseに保存（Upsert）。フロント側へ返すテキストからはタグが完全に除去される。
- **孤食ロボット機能（食事記録・声かけ・ゆるいアドバイス）:** Wikipedia「孤食ロボット」を参考に実装。①「ご飯食べた」等の発話を検知し、LLMで内容を整理して `meal_logs` テーブルに記録する。②朝食(6-10時)/昼食(11-14時)/夕食(17-21時)の時間帯に、その食事の記録が今日まだ無ければ「一緒に食べている気分」になれる一言を自発的に届ける（カレンダー先回り提案と同方式で、Render無料プランのスリープ対策済み）。③直近の食事記録を会話コンテキストに自動的に織り込み、説教にならない範囲でゆるい提案をする（「最近コンビニ多いけど、たまには一緒に作ってみる？」）。④📷ボタンで食事の話をしながら撮ると、Vision解析で写真を `meal_logs` に紐付け、「今日のごはん見せて」で振り返れる。
- **読書通帳機能（バーコード記帳・会話での読み出し。2026-06-28〜29追加）:** 図書館の読書通帳ATMをAR上で再現した機能。①マーカーロスト中（顔アイコン表示中）にのみ📔ボタンが出現し、タップするとバーコードスキャンモーダルが開く。②既存のMindARカメラ映像（`<video>`要素）から直接フレームを読み取り `@zxing/browser` でISBNをデコードするため、`getUserMedia` の新規呼び出しやMindARの停止・再開は一切発生しない（カメラ専有の競合や黒帯バグの再発を避ける設計）。③検出したISBNを国立国会図書館サーチAPI（NDLサーチ・APIキー不要）→ヒットしなければGoogle Books APIの順で照会し、タイトル・著者・出版社・シリーズ名（`dcndl:seriesTitle`）・出版年（`dcterms:issued`）を取得する（定価・表紙画像は両APIとも基本的に持たないため手入力で補完）。④確認画面で内容を確認し「記帳する」を押すと `reading_logs` テーブルに保存される。同じ本（ISBN一致、無ければタイトル完全一致）を再度記帳した場合は新規行を作らず、既存行の `borrow_count` を+1し `borrowed_at` を最新の日付に更新する（再読・再度借りた場合の記録として扱う）。⑤「いつその本借りた？」「最近何冊読んだ？」「合計いくら分読んだ？」のような質問は `get_book_history` ToolとしてSynthesizerの `bind_tools` に登録されており、`get_my_schedule` と同様に聞かれたときだけ呼ばれる（雑談中に余計なDBアクセスは発生しない）。バーコードが読み取れない場合のISBN手入力フォールバックも用意している。
  - **表紙画像（`cover_url`）は常に`NULL`。** NDLサーチは書影を提供せず、NDLサーチヒット時にGoogle Booksへ表紙だけ追加照会する実装を2026-06-28に一度試したが、Google Books APIキー無し運用がレート制限(429)に恒常的に当たることが実機検証で判明したため撤去した。`cover_url`列自体は将来別の書影ソースを試す可能性に備えてテーブル上は残している。
  - **ジャンル（`genre`）は基本的に`NULL`。** NDLサーチの`dcndl:genre`タグは漫画など一部のジャンル区分にしか付与されず、一般書籍（小説・ビジネス書・技術書等）には存在しない。Google Booksの`categories`は代替として一応取得を試みるが、429に当たりやすく精度もまちまち。ジャンル分類を実装するなら、NDLサーチの`dc:subject`（NDC10コード、例: `989.53`）の先頭1桁を日本十進分類法の第1次区分表（0:総記〜9:文学の10分類）に変換する方式が、著作権上も実装コスト上も最も筋が良いという調査結果が出ている（未実装。次回着手時の指針として記録）。
- **ルキルキ マインドプロファイル（キャラクターバイブル。2026-06-29追加）:** ルキルキというキャラクターのアイデンティティを、将来AI動画生成や他者の脚本へのキャスティング素材として使えるMarkdown（`ruki_mind/YYYY-MM.md`）として月次で蓄積する機能。MagatokiLabの方針として、ユーザーが読んだ本・食べた食事・経験した出来事は「ユーザーの行動データ」ではなく「ルキルキ自身が経験したこと」として一人称的に語り直す（`reading_logs`/`meal_logs`/`episode_memories`を入力データとして使用）。生成は「Curator」という、ユーザーには見えない裏方専属のAIエージェント（`persona/curator_persona.md`）が担当し、ルキルキ本体の会話エージェント（`synthesizer_node`等）とは完全に独立しているため、ユーザーとの通常の会話フローには一切影響しない。出力フォーマットは「ログライン」「揺らがない核」「話し方のサンプル」「矛盾・質感」「関係性の振る舞い方」「興味・好みの変遷」「絶対にやらないこと」「ビジュアル的な反応傾向」「キャスティングノート（Curator所見）」の9項目で構成され、データが無い項目は捏造せず「まだ観測されていない」と正直に書くことをCuratorペルソナで厳格に指示している（キャラクターIPとしての信頼性を守るため）。版を残す方式（`YYYY-MM.md`は上書きしない）で、前月分があれば変化の比較材料としてCuratorに渡す。手動トリガー用に`GET/POST /api/internal/generate_mind_profile`（生成）と`GET /api/internal/view_mind_profile`（プレーンテキストでの確認用）をユーザー向け会話フローとは別経路で用意している。
  - **「話し方のサンプル」は実発言ログが無いため、現状ほぼ「観測されていません」になる。** 当初の実装ではエピソード記憶の要約から「言いそうな台詞」をCuratorが作文してしまう問題があったため、2026-06-29に「会話ログの引用がある場合のみ書く。無ければ正直にそう書く」という指示に強化した。実際の発話ログを収集する仕組み（会話履歴の保存・引用）は未実装で、次の拡張ポイントとして残っている。
  - **AI動画生成プロンプトへの変換ルールは `ruki_mind/_PromptBuilder/00_builder.md` に実装・実機検証済み。** 抽象語（「映画的」「美しい」等）を禁止し「誰が・どこで・何をして・どちらを向いて・カメラ・時間変化」の6要素を必須にするルール、ルキルキ固有の感情表現・姿勢・カメラワークのガイド、シーンタイプ別テンプレートを含む。見た目リファレンス画像（`ruki_mind/reference_images/`）も2026-07-14に整備完了。詳細は下記「動画プロンプトビルダー」の項目を参照。
- **モバイルでの意図しないズーム対策:** `viewport` 設定（`maximumScale: 1`、`userScalable: false`）と、テキスト入力欄のフォントサイズを16px以上にすることで、iOS Safari特有の「入力欄フォーカス時の自動ズーム」とピンチズームの両方を抑制している。
- **会話を質問で締めくくらない:** 返答の最後を「〜どうですか？」のような問いかけで終えると、ユーザーが「答えなきゃ」と気を遣ってしまう問題があったため、`rukiruki_persona.md`に独立した見出しで明記し、`persona.py`の`SYSTEM_CONSTRAINTS`にも同趣旨の制約を重ねて追加した。意味が分からず聞き返す必要がある場合のみ例外とし、それ以外は感想・意見・相槌だけで言い切るよう指示している。
- **Gemini⇄OpenAI自動フォールバック（ResilientLLM。2026-07-05追加）:** Router/Agent/Evaluator/Visionで使うGemini（無料枠）が429（レート制限）や404（モデル未対応）等で失敗した場合、`services/resilient_llm.py`が自動的にOpenAI（デフォルト`gpt-4o-mini`）へ切り替えて再試行する。無料枠を使い切ってもチャット機能自体が止まらないようにするための保険。スナップ写真生成（Nano Banana）にも同様にOpenAI `gpt-image-1`へのフォールバックを用意している。
- **Xシェア機能（2026-07-06追加）:** 会話テキスト・スナップ写真をX（旧Twitter）にシェアするボタンを追加。X公式APIは2026年2月に無料枠が実質廃止（従量課金制）されたため、`lib/share.ts`でWeb Share API（画像添付対応、iOS Safari等）とWeb Intent（テキストのみ、フォールバック）のみを使う設計にした。
- **キャラクター参照画像ライブラリ（2026-07-06追加）:** 将来のAI動画生成（Gemini Omni Flash等の被写体参照機能）に備え、スナップ生成が成功するたびに`character_references`テーブルへ記録する。`SNAP_POSES`（正面・陽気）に加え、複数アングル・落ち着いた表情を意識した`REFERENCE_POSES`を新設し、`REFERENCE_POSE_RATIO`（デフォルト20%）の確率で混ぜる。
- **エピソード記憶の映像的描写（2026-07-06追加、2026-07-14に会話コンテキストへ組み込み）:** `services/memory.py`の`maybe_save_episode()`に`visual_description`フィールドを追加。既存のキーワード抽出LLM呼び出しに相乗りする形で実装しており、追加のAPI呼び出しは発生しない。当初は「将来のAI動画生成向けの素材」としてDBにのみ蓄積し日常会話のプロンプトには含めない設計だったが、「画面を見ていない前提で会話する」という方針（後述）のもと、2026-07-14に`get_recent_episodes()`のコンテキスト構築へ組み込み、ルキルキが写真の中身を言葉で説明できるようにした。
- **2キャラクター同時スナップ（2026-07-06追加）:** 「IZANAとAcielとスナップ」のように2人を同時にスナップできる。フロントエンド（`useChat.ts`）の正規表現で「と」の出現回数から1人/2人を判定し、`services/snap.py`の`DUO_POSES`（向き合って会話する等、2人の相互作用を明示的に記述したポーズ集）で生成する。初期実装では①相互作用が生まれない②スケール感が破綻する③T-poseが頻発する、という3つの課題があったが、プロンプト改善とリファレンス画像の差し替え（腕組み等のアクションポーズに変更）で解決した。キャラクター参照画像を作る際の注意点（1枚1ポーズ・腕を浮かせない・全身を入れる等）も確立している。
- **シーン参照ライブラリ（2026-07-10追加）:** `character_references`が「見た目」を記録するのに対し、`scene_references`は「どんな場面・どんな相互作用だったか」を記録する。1人・2人スナップを問わず全件記録し、`pose`のテキストがそのままシチュエーション記述を兼ねるため追加コストは無い。
- **パスワード認証フォールバック（2026-07-10追加）:** 屋外の実機テストでMetaMask接続が不安定になる事象への対応として、`SBTAuthGate.tsx`にウォレット認証を迂回できる簡易パスワード認証を追加。検証はバックエンド（`/api/auth/password`）で行い、環境変数`TEST_ACCESS_PASSWORD`と平文比較する。テスト運用限定で、本番公開時は環境変数を削除して無効化する前提。
- **ルキルキ本人のキャラクターシート作成（2026-07-14追加）:** `context/images/`に他4キャラのみ存在し主役のルキルキ自身の2D画像リファレンスが無い、という課題を解消。Blender MCPをローカルBlenderに接続し、`avatar.glb`から正面/45度/側面/バストアップ＋表情5種（`Fcl_ALL_*`シェイプキー使用）の計8カットを自動レンダリングし、`ruki_mind/reference_images/`に配置した。
- **動画プロンプトビルダー（2026-07-13〜14追加）:** `scene_references`（過去のスナップ写真）を元に、AI動画生成（Kling / Pollo AI / Gemini等）向けの日本語・英語プロンプトを自動生成する機能。`services/prompt_builder.py`・`video_prompts`テーブル・スタンドアロン管理UI（`public/tools/prompt_builder_ui.html`）から成る。
  - **写真グラウンディング：** `scene_references.image_url`（実際のスナップ写真）をLLMへ画像として直接渡し、背景描写の最優先情報源とする。これにより、写真の内容と無関係な背景を毎回発明してしまう問題と、プロンプトが似通いがちな問題を同時に解消した。
  - **他キャラのシチュエーションをルキルキに主演させる機能：** `member_names`を見て他キャラ（DrOhma等）のシーンと判定した場合、写真は背景情報としてのみ使い、写っている人物の外見は無視してルキルキ本人の参照画像（2枚目の画像として同時に渡す）で描写する。`feature_rukiruki=True`で明示指定も可能。
  - **会話内での呼び出し（`get_video_prompt_memories`・`||SHOW_VIDEO||`タグ）：** 「あの動画見せて」と話しかけると、`get_book_history`と同型のLangChain Toolで該当プロンプトを検索し、結果動画があれば`VideoViewer.tsx`（`SnapViewer.tsx`と同型）で再生する。実機検証でGPT-4oが生URLを含むタグの出力を渋る挙動が判明したため、タグには`video_prompts.id`（短い数字）のみを入れさせ、実URLへの解決は`get_video_url_by_id()`でPython側が確定的に行う設計にしている。
  - **00_builder.mdの実機検証（2026-07-13〜14）：** Pollo AI・Klingでの実機テストを通じて、①表情抑制は否定形の具体例を倍量で書かないと弱く伝わる、②「固定」カメラの指示だけでは自動ズーム/パンが入りやすい、③「夜・路地裏・薄暗い・一人きり」の組み合わせは動画生成モデルの未成年保護ガードレールに引っかかりやすい、④英語プロンプトの主語に"a girl"を使うとルキルキ（設定上は男の子、外見は中性的）が女性寄りに描写されやすい、という4つの実践的知見を得てルール化した。詳細は`ArtAR_ルキルキ_技術リファレンス.html`のトラブルシュート履歴25番を参照。
- **「一緒にいる感覚」の強化（2026-07-14追加）:** スマートグラス展開という長期構想を見据え、「目を使わなくても相棒として成立するか」を基準に既存実装を見直した一連の変更。
  - **画面を見ていない前提での会話対応：** `persona.py`に、`||SHOW_IMAGE||`/`||SHOW_VIDEO||`タグを使う際は中身を必ず言葉で説明するルールを追加（従来は「こんな感じでしたよ！」のような中身の無い相槌で終わっていた）。合わせて`visual_description`（前述）を会話コンテキストへ組み込んだ。
  - **アルバム機能（`services/timeline.py`）：** `episode_memories`（写真）・`video_prompts`（採用済み動画）・`ruki_mind/*.md`（月次マインドプロファイル）を横断し、時系列で統合するタイムラインを新設。閲覧専用で、スタンドアロン管理UI（`public/tools/album_ui.html`）から確認する。
  - **変化メモ機能（`generate_growth_note()` / `get_latest_growth_note()`）：** 「たまごっち的な育成感も出したいが、レベルアップ等のゲーム的表現は対等な相棒という設計思想と衝突する」という方針のもと、前月との変化をルキルキ自身が一人称でごく短く（1〜2文）語れるようにした。`generate_mind_profile()`が既に前月分を比較材料としてCuratorへ渡す設計だったことを活用し、独立した専用LLM呼び出しとして実装。`ruki_mind/_growth_notes/YYYY-MM.txt`に版を残す方式で保存し、`services/emotion.py`の`get_growth_context()`経由で会話に混ぜる（「毎回言う必要はない」トーンを踏襲）。詳細は`ArtAR_ルキルキ_技術リファレンス.html`のトラブルシュート履歴26番を参照。
- **映画通帳（2026-07-17追加）:** 読書通帳と対になる機能。ルキルキが「AI俳優を目指している」という設定に基づき、鑑賞した映画の知識（監督・ジャンル・あらすじ）を蓄積する。`services/movies.py`・`movie_logs`テーブルから成り、読書通帳と違い記帳の起点はバーコードではなく**会話ベース**（「〇〇観た」）。書き込み系Tool（`log_watched_movie`）としては本システム初の実装で、`persona.py`側で「観たと明言した時だけ」という誤爆防止の縛りを強めにかけている。
  - **TMDb認証はv4 Bearer token採用：** 当初v3の`?api_key=xxx`方式で実装したが、URLパラメータに乗るためログに残るリスクがあり、TMDb公式もBearer token（v4、`Authorization`ヘッダー）を推奨方式としているため切り替えた。環境変数名は`TMDB_API_READ_ACCESS_TOKEN`。
- **アルバムの読書・映画フィルター拡張（2026-07-17追加）:** `services/timeline.py`に`include_reading`/`include_movies`パラメータを追加し、`books.py`/`movies.py`の既存関数を再利用して読書通帳・映画通帳もタイムラインに統合可能にした。デフォルトはFalse（写真・動画・成長のみ、従来通りの挙動）。`album_ui.html`はAPIを1回だけ全種類込みで呼び、表示の絞り込みはフィルターチップでクライアント側（JS）が行う設計にし、チップの切り替えに再取得のラグが出ないようにしている。
- **食事の記録ページ（`public/tools/meals_ui.html`。2026-07-17追加）:** 当初アルバムに統合する案も検討したが、「写真・動画・成長」という“一緒にいた瞬間”の時系列に食事記録を混ぜると、時系列表示という見せ方そのものが食生活の可視化・監視のような圧を生みかねないと判断し、**意図的に別ページとして分離**した。`healthiness`（食事の健康度合い）フィールドもUIには表示せず、`persona.py`が守っている「栄養指導・説教はしない」という会話ポリシーとページの見た目が矛盾しないようにしている。
  - **バグ修正：** 写真付きの食事記録（`_extract_and_save_meal_log_with_photo`）が、Visionで実際に画像を解析していたにもかかわらず、`save_meal_log()`呼び出し時に`image_url`を渡し忘れており、写真がDBに保存されていなかった不具合を修正（2026-07-17）。
- **カメラストリームのバックグラウンド復帰対応（`hooks/useAR.ts`。2026-07-17追加）:** 外出先で「この場所を記憶して」を試すと、写真保存が`image_base64が無いためスキップ`という理由で繰り返し失敗する不具合が発覚。原因はモバイルブラウザが、タブがバックグラウンド化（画面ロック・他アプリへの切り替え等）した際にカメラの`<video>`ストリームを一時停止することがあり、`useAR.ts`にはタブ再表示時の再開処理が無かったこと。`visibilitychange`イベントを監視し、タブ復帰時に一時停止中の`<video>`があれば`.play()`し直す処理を追加した。iOS等でカメラトラック自体が完全に切断されるケースには未対応（軽い方の修正で様子見中）。
- **既知の紛らわしさ：「登録して」と「記憶して」は別機能（2026-07-17に切り分け）:** `main.py`の`register_keywords`（`memory_spots`テーブルへの名前付きスポット登録。名前→読み方を聞く2ターンの会話フローが起動する）は「登録して」「刻んで」等の限定的なキーワード一致でのみ発火する。一方`persona.py`の`||SAVE_PHOTO||`ルール（今のカメラ映像を1枚だけ写真保存する）は「記憶して」「覚えておいて」という言い回しで発火する。「この場所を**記録**して」のように似た言葉を使うと、`register_keywords`にはヒットせず`SAVE_PHOTO`側が発火してしまい、名前付きスポットとしては登録されない。両者はコード上バグなく設計通りに動いているが、言葉の選び方次第で挙動が変わるため、名前付きスポットとして残したい時は必ず「**登録して**」と言う必要がある。
- **近隣スポット推薦（`services/places.py`。2026-07-31追加）:** Google Places API (New) の`searchNearby`エンドポイントで、現在地周辺のカフェ・観光地・レストラン等を案内する。`services/location.py`（今どこにいるか＝逆ジオコーディング）とは責務を分離し、こちらは「周辺に何があるか」専任。`find_nearby_places`はLangChain Toolとして実装し、`locate_current_position`と同じくシステムプロンプトに提示された現在座標をLLMが引数に渡す（省略時はサーバー側でstateの生座標にフォールバック）。要環境変数`GOOGLE_PLACES_API_KEY`。
- **軽量リマインダー（`services/reminders.py`。2026-07-31追加）:** Googleカレンダーに乗せるほどではない「レポート提出」レベルの単発タスクを会話ベースで登録・確認・完了できる機能。`set_reminder`/`get_my_reminders`/`complete_reminder`の3Tool（`log_watched_movie`に続く本システム2例目の書き込み系Tool群）。自発通知（`reminder_prep_job`）は`calendar_prep_job`と違い「期限が近ければ常に知らせるべき」という前提のもと、LLM判定を挟まず`notified_at`列で一生に一度だけ通知する設計。`[INITIAL_GREETING]`から29秒遅延で発火。
- **記憶ベース画面（`services/profile.py` + `public/tools/memory_base_ui.html`。2026-07-31追加）:** ユーザー向けの「マイプロフィール」集計画面。`ruki_mind`がルキルキ自身の人格を記録するのに対し、こちらは既存の`memory_spots`/`episode_memories`/`reading_logs`/`movie_logs`/`meal_logs`/`user_growth_notes`を横断集計するだけで、新規テーブルは`user_growth_notes`以外不要（`timeline.py`と同じ設計思想）。UIは`album_ui.html`のデザイン言語（和紙調パレット・フィルターチップ・`window.storage`でのURL保存）を踏襲。表示専用。
- **天気ベース自発提案（`services/weather_advisor.py`。2026-07-31追加、実機未検証）:** OpenWeatherMapの5日間/3時間ごと予報から、直近30時間以内の雨・雪を検知し「傘を持っていくの忘れないでね」のように自発的に一言提案する。`services/state.py`の`weather_cache`に`lat`/`lng`を追加し、会話時に最後にキャッシュされた座標を定期ジョブから流用する設計（そのため、アプリ起動後まだ一度も位置情報付きの会話が発生していない場合は発火しない）。判定は天気コードの機械的な照合のみで完結し追加のLLM呼び出しは発生しない。`app_state`で12時間間隔制御（`calendar.py`と同じ作法）。`[INITIAL_GREETING]`から36秒遅延で発火。
- **ユーザー成長記録（`services/user_growth.py`。2026-07-31追加）:** `character_bible.py`の`generate_growth_note()`（ルキルキ自身の変化をAIが過去ログから自動推測して生成）と向きが逆の機能。**ユーザーが自分から語った成長・自慢だけ**を記録し、行動ログからAIが推測して記録することは`persona.py`の使用ルールで明示的に禁止している（本人が言っていないことを言い当てる形になり的外れになりやすいため）。`log_user_growth`/`get_user_growth_notes`の2Tool。`profile.py`の記憶ベースにも統合され、ルキルキ視点の変化メモとは別枠（🌱「自慢」）で表示される。
- **フォールバック使用時のキャラクター内言及（2026-07-05追加）:** 上記のフォールバックが発生した回だけ、ルキルキの返答の末尾に「無料枠を使い切っちゃった」旨のセリフをランダムに1つ追加する。Chronicle/Keeper/Pulse/Evaluatorのいずれかでフォールバックが起きたことを`state`経由で検知しており、Supabaseへのエピソード記憶保存はこのセリフを追加する前の文章で行うため、会話履歴にノイズは残らない。Router（構造化出力）のフォールバックはこの仕組みでは検知できない技術的制約がある。

---

## 💰 AI利用コストの構成と最適化方針

**2026-07-05更新：** Router/Agent/Evaluator/Vision/スナップ写真生成をOpenAIからGeminiへ部分移行した。「Synthesizerの人格は絶対に変えない」という条件のもと、判定系タスクのみをコスト最適化の対象とした（全面移行は見送り。判断の経緯は`ArtAR_ルキルキ_技術リファレンス.html`のトラブルシュート履歴22番を参照）。

| 箇所 | モデル | 発生タイミング | 備考 |
|---|---|---|---|
| Synthesizer（メイン会話生成） | `gpt-4o`（`LLM_MODEL_SMART`、OpenAI固定） | ユーザーが話しかけるたび | ルキルキの口調・性格の核心部分のため、移行対象外（最後の手段として温存） |
| Router（意図分類） | `gemini-2.5-flash-lite`（`LLM_MODEL_FAST`） | 同上 | **2026-07-05、`gpt-4o-mini`から変更。** 失敗時はOpenAI `gpt-4o-mini`へ自動フォールバック（`services/resilient_llm.py`） |
| Chronicle/Keeper/Pulse/Evaluator | `gemini-2.5-flash-lite`（`LLM_MODEL_FAST`） | 同上 | 同上。3並列実行のため1ターンで最低3回同時にGeminiコールが発生する点に注意（RPMの消費が早い） |
| Vision（食事写真の食べ物判定） | `gemini-2.5-flash-lite`（`LLM_MODEL_FAST`） | 📷ボタン押下時、食事の発話と共に撮影された場合のみ | 同上。画像コンテンツは辞書形式（`{"url":...}`）で統一し、OpenAIフォールバック時も動作するようにしている |
| スナップ写真生成 | `gemini-2.5-flash-image`（Nano Banana） | 「○○とスナップ」コマンド時 | 2026-07-05、`gpt-image-1`から変更。失敗時はOpenAI `gpt-image-1`へ自動フォールバック |
| AI情報ダイジェスト | `gemini-2.5-flash-lite`＋Tavily検索 | 2026-06-29、日次から週次（7日間隔）に変更 | `should_generate_ai_news_today()` |
| Curator（マインドプロファイル生成） | `LLM_MODEL_SMART`にフォールバック（`CHARACTER_BIBLE_MODEL`で個別上書き可、実質`gpt-4o`、OpenAI固定） | 月1回・手動トリガー | ルキルキの自己認識を書くタスクのため、Synthesizerと同様に移行対象外とした |

**Gemini無料枠が切れたらどうなるか：** `services/resilient_llm.py`のResilientLLMが自動的にOpenAI（デフォルト`gpt-4o-mini`、`FALLBACK_MODEL_FAST`で変更可）へ切り替えるため、チャット機能自体は止まらない。ただしフォールバックが多発するとOpenAI課金がじわじわ増えるため、Renderのログで`Gemini失敗 → OpenAI`という行がどれくらいの頻度で出るかを定期的に確認するとよい。頻発するようであれば、Google Cloud Billingを有効化してGeminiの無料枠自体を引き上げる、または3並列（Chronicle/Keeper/Pulse）の呼び出し数を見直すといった対策を検討する。

**今後の検討事項（ローカルLLM）:** Router・Evaluatorのような判定系タスクは、将来的にLlama 3.1 8B等のローカルLLM（Ollama等）への置き換えも選択肢としては残るが、2026-07-05時点ではGeminiの無料枠＋OpenAIフォールバックという構成でコストと可用性のバランスが取れていると判断し、優先度は下げている。実行環境（どこでローカルLLMを動かすか）の設計が前提として必要な点も変わらない。

**Gemini Omni Flash（動画生成）の調査（2026-07-06）と、その後の方針転換（2026-07-13〜14）:** 2026年6月30日公開の動画生成・編集モデルを調査した際は、コスト（約$0.10/秒）や規約上の制約から機能実装を見送り、素材の蓄積を優先する方針だった。その後、ルキルキ本人のキャラクターシートが整い、`scene_references`に十分な素材が蓄積されたことを受け、**動画生成そのものはアプリに組み込まず「プロンプト生成まで」に留める**設計方針で実装に着手した。実際の動画生成リクエスト（Kling / Pollo AI / Gemini等）は引き続き手動操作とし、コスト管理と生成結果のレビュー品質を人力に委ねている。生成されたプロンプトと実機テストの結果（採用/却下・使用モデル・結果動画URL）は`video_prompts`テーブルに蓄積され、会話内から「あの動画見せて」のように呼び出せる。詳細は`ArtAR_ルキルキ_技術リファレンス.html`のトラブルシュート履歴25番を参照。

**2キャラクター同時スナップとリファレンス画像の教訓（2026-07-06〜07-10）:** 「IZANAとAcielとスナップ」のように2人同時にスナップできる機能を実装。相互作用の欠如・スケール感の破綻・T-poseという3つの課題に遭遇したが、最終的な原因は**リファレンス画像の作り方**にあると判明した（3Dキャラクターシートの「正面ターンアラウンド」を使うと、腕を浮かせた基本姿勢がモデルに刷り込まれる）。今後キャラクターを追加する際は、1枚1ポーズ・腕を体から離さない・全身を入れる、という基準を踏襲する。詳細は同資料のトラブルシュート履歴24番を参照。

**一般公開向けプレゼン資料:** `presentation.html`（スタンドアロンHTML）を2026-06-30に作成。AIやWeb3に興味があるビジネス層向けに「AIキャラクターを育てて、プロンプト資産にする」というコンセプトを訴求する内容。ブラウザで直接開くだけで使える（依存ファイルなし）。

---

より詳細な仕様（各サービスファイルの実装、タグの一覧、過去のトラブルシュート履歴）は `ArtAR_ルキルキ_技術リファレンス.html` を参照してください。
