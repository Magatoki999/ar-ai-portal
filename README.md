# MagatokiLab XR Gateway & WebAR Portal

本プロジェクトは、オリジナルプロジェクト『MagatokiLab』における、WebARを用いたキャラクターの現実世界への召喚、およびLLM/TTSを活用したリアルタイム対話・記憶システムのフルスタックリポジトリです。

## 🎭 召喚対象キャラクター
* **名前:** ルキルキ (RukiRuki) / コードナンバー: ML-001
* **背景:** バーチャルSNS「cluster」に5年以上存在する古参住人。仮想空間と現実空間の境界を観測する役割を持つ。
* **特徴:** 最新技術やネットカルチャー、Web3、そして京都文化に極めて精通している。

---

## 🏗️ システムアーキテクチャ

本システムは以下の4つのレイヤーで構成されています。

| コンポーネント | 技術スタック / サービス | 役割・詳細仕様 |
| :--- | :--- | :--- |
| **Web3認証ゲート** | wagmi / RainbowKit (Polygon) | Polygon上のSBT（Soulbound Token）の保有数を検証。保有者のみARビューアへ誘導。 |
| **フロントエンド** | Next.js / MindAR / Three.js | マーカー認識によるAR表示、3Dアバターの描画とリアルタイム・リップシンク制御。 |
| **バックエンド** | FastAPI / LangChain | APIサーバー。動的コンテキスト生成、LLM応答、音声合成プロバイダーの制御。 |
| **LLM / TTS** | OpenAI (gpt-4o-mini) / ElevenLabs | ルキルキのペルソナを適用した対話生成[cite: 1, 2, 3]。ElevenLabs失敗時はOpenAI TTSへ自動フォールバック[cite: 2, 3]。 |
| **データベース** | Supabase (REST API) | ウォレットアドレスに紐づくユーザー名（user_name）の永続化[cite: 2, 3]。 |

---

## 📁 ディレクトリ構造

```text
.
├── backend/     # FastAPI (AI対話・音声合成・記憶処理)
└── frontend/    # Next.js (Web3認証ゲート・MindARビューア)