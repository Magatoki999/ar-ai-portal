// lib/types.ts
// ─────────────────────────────────────────────────────────────────────────────
// アプリ全体で共有する型定義・定数。
// すべての hooks / components はここから import する。
// ─────────────────────────────────────────────────────────────────────────────

export type AIStatus    = "idle" | "thinking" | "talking";
export type SearchPhase = "OFFLINE" | "STABLE" | "CONNECTING..." | "TAVILY_SEARCHING..." | "DATA_ANALYZING...";

// RukiFaceIcon（マーカーロスト中の顔アイコン）の表情。2026-06-26追加。
// evaluator_node がセリフの意味合いから分類する。thinking中は使われず、
// aiStatus が "talking" のときだけこの値で表情が変わる（idle中はneutral相当の固定表示）。
export type FacialEmotion = "fun" | "sad" | "worry" | "angry" | "neutral";

export interface MorphTargetRef {
  mesh: any;
  idxs: number[];
}

export interface HistoryItem {
  role: "user" | "ruki";
  text: string;
  timestamp: string;
}

// ── チャット API レスポンス ──
export interface ChatApiResponse {
  reply:             string;
  audio_data:        string | null;
  audio_mime?:       string;
  spatial_effect:    string;
  spot_proposal:     string;
  arweave_tx_id:     string;
  show_image_url:    string;
  engrave_triggered: boolean;
  facial_emotion?:   FacialEmotion;
  status:            string;
}

// ── TTS API レスポンス ──
export interface TTSApiResponse {
  audio_data: string | null;
}

// ── スナップ API レスポンス ──
export interface SnapApiResponse {
  status:      "ok" | "error";
  image_url?:  string;
  member_name?: string;
  message?:    string;
}

// ── WebSocket メッセージ ──
export interface WsProactiveSpeech {
  type:           "proactive_speech";
  reply:          string;
  audio_data:     string | null;
  audio_mime?:    string;
  spatial_effect: string;
  facial_emotion?: FacialEmotion;
}

export interface WsStatusUpdate {
  type:    "status";
  status:  string;
  text?:   string;
}

export type WsMessage = WsProactiveSpeech | WsStatusUpdate | { type: string };
