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
| **会話AI** | LangGraph（Router → Chronicle/Keeper/Pulse → Synthesizer → Evaluator） | OpenAI `gpt-4o`（Synthesizer）/ `gpt-4o-mini`（Router/Agent/Evaluator）。2026-06-29、コスト最適化のためRouterを`gpt-4o`から`gpt-4o-mini`へ変更。 |
| **音声合成（TTS）** | Gemini TTS（メイン） / OpenAI TTS（フォールバック） / ElevenLabs（オプション） | `TTS_PROVIDER` 環境変数で切替。デフォルトは `gemini`。 |
| **画像生成** | OpenAI `gpt-image-1` | 「○○とスナップ」コマンドによる記念写真合成。 |
| **外部検索** | Tavily Search | 雑談や手持ち知識で解決できない場合のみ限定的に使用。 |
| **データベース / ストレージ** | Supabase（PostgREST + Storage） | エピソード記憶、メモリースポット、ユーザープロフィール、汎用キーバリュー(`app_state`)、AI情報ダイジェスト(`ai_news_digest`)、食事記録(`meal_logs`)、読書記録(`reading_logs`)、画像。 |
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
│   │   └── character_bible.py  # マインドプロファイル生成バッチ（Curator呼び出し。2026-06-29追加）
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
│       │   └── BookScanModal.tsx   # 読書通帳：バーコードスキャンモーダル（2026-06-28追加）
│       ├── hooks/               # useAR / useChat / useVoice / useWebSocket
│       └── lib/                 # types.ts / audio.ts
├── public/                    # ルート直下（src/ と同階層）
│   ├── avatar.glb / targets.mind
│   ├── ruki_appear.wav        # ※未使用。コードから参照なし
│   └── images/                 # idle/talking/thinking/fun/sad/worry/angryの顔アイコン画像
├── ruki_mind/                  # ⚠️ ルート直下（backend/の外）。Curatorの出力先（2026-06-29追加）
│   ├── YYYY-MM.md               # 月次マインドプロファイル（版を残す方式・上書きしない）
│   ├── _PromptBuilder/
│   │   └── 00_builder.md        # AI動画生成プロンプトへの変換ルール（下書き・未検証）
│   └── reference_images/        # 見た目リファレンス画像（未整備）
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
  - **将来のAI動画生成プロンプトへの変換ルールは `ruki_mind/_PromptBuilder/00_builder.md` に下書きとして用意済み（未検証）。** 抽象語（「映画的」「美しい」等）を禁止し「誰が・どこで・何をして・どちらを向いて・カメラ・時間変化」の6要素を必須にするルール、ルキルキ固有の感情表現・姿勢・カメラワークのガイド、シーンタイプ別テンプレートを含む。見た目リファレンス画像（`ruki_mind/reference_images/`）はまだ未整備（GLBモデルからの静止画書き出しが必要）。
- **モバイルでの意図しないズーム対策:** `viewport` 設定（`maximumScale: 1`、`userScalable: false`）と、テキスト入力欄のフォントサイズを16px以上にすることで、iOS Safari特有の「入力欄フォーカス時の自動ズーム」とピンチズームの両方を抑制している。
- **会話を質問で締めくくらない:** 返答の最後を「〜どうですか？」のような問いかけで終えると、ユーザーが「答えなきゃ」と気を遣ってしまう問題があったため、`rukiruki_persona.md`に独立した見出しで明記し、`persona.py`の`SYSTEM_CONSTRAINTS`にも同趣旨の制約を重ねて追加した。意味が分からず聞き返す必要がある場合のみ例外とし、それ以外は感想・意見・相槌だけで言い切るよう指示している。

---

## 💰 AI利用コストの構成と最適化方針

OpenAI APIの利用枠を消費する箇所は以下の通り。月々のコストを抑えたい場合の参考に。

| 箇所 | モデル | 発生タイミング | 備考 |
|---|---|---|---|
| Synthesizer（メイン会話生成） | `gpt-4o` | ユーザーが話しかけるたび | ルキルキの口調・性格の核心部分のため、コスト最適化の対象外（最後の手段として温存） |
| Router（意図分類） | `gpt-4o-mini` | 同上 | **2026-06-29、`gpt-4o`から変更。** 構造化出力（Literal型での分類）のみのタスクのため精度への影響は小さいと判断 |
| Agent / Evaluator | `gpt-4o-mini` | 同上 | 元から軽量モデル |
| Vision（食事写真の食べ物判定） | `gpt-4o-mini` | 📷ボタン押下時、食事の発話と共に撮影された場合のみ | `detail: "low"`でさらにコスト抑制済み |
| スナップ写真生成 | `gpt-image-1` | 「○○とスナップ」コマンド時 | 画像生成は他のテキスト系APIと比べて単価が高い。使用頻度に注意 |
| AI情報ダイジェスト | `gpt-4o-mini`＋Tavily検索 | **2026-06-29、日次から週次（7日間隔）に変更** | `should_generate_ai_news_today()` |
| Curator（マインドプロファイル生成） | `gpt-4o-mini`（環境変数`CHARACTER_BIBLE_MODEL`で上書き可） | 月1回・手動トリガー | 2026-06-29、デフォルトを`gpt-4o`から変更。長文の人格分析タスクのため、生成結果の質が気になる場合は`CHARACTER_BIBLE_MODEL=gpt-4o`で個別に戻せる |

**今後の検討事項（ローカルLLM）:** Router・Evaluatorのような判定系タスクは、将来的にLlama 3.1 8B等のローカルLLM（Ollama等）への置き換えが現実的と考えられる。Synthesizer（メイン会話）はルキルキらしさの根幹のため、ローカル化の優先度は最も低い。実行環境（どこでローカルLLMを動かすか）の設計が前提として必要なため、現時点では未着手。

---

より詳細な仕様（各サービスファイルの実装、タグの一覧、過去のトラブルシュート履歴）は `ArtAR_ルキルキ_技術リファレンス.html` を参照してください。
