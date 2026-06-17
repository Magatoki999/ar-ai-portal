// hooks/useChat.ts
// ─────────────────────────────────────────────────────────────────────────────
// チャット送受信・スナップ生成・記憶写真保存を管理するフック。
// 責務:
//   - /api/chat  へのメッセージ送信と API レスポンス処理
//   - /api/snap  へのスナップ生成リクエスト
//   - /api/memory/photo へのカメラフレーム保存
//   - /api/tts   を使った固定挨拶・初期挨拶の再生
//   - GPS 取得・カメラキャプチャの呼び出し
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useState, useRef } from "react";
import type {
  AIStatus,
  HistoryItem,
  SearchPhase,
  ChatApiResponse,
} from "../lib/types";
import { base64ToAudioUrl, resolveAudioMime } from "../lib/audio";

interface UseChatOptions {
  containerRef:       React.MutableRefObject<HTMLDivElement | null>;
  inputRef:           React.MutableRefObject<HTMLInputElement | null>;
  audioInstanceRef:   React.MutableRefObject<HTMLAudioElement | null>;
  timersRef:          React.MutableRefObject<NodeJS.Timeout[]>;
  currentEffectRef:   React.MutableRefObject<string>;
  addressRef:         React.MutableRefObject<string | undefined>;
  address?:           string;
  initAudioPipeline:  (audio: HTMLAudioElement) => void;
  playAudio:          (b64: string, mime?: string, onEnded?: () => void) => Promise<void>;
  stopAudio:          () => void;
  onAiStatusChange:   (status: AIStatus) => void;
  onSearchPhaseChange:(phase: SearchPhase) => void;
  onSubtitleChange:   (text: string) => void;
  onSpatialEffect:    (effect: string) => void;
  onEngraveToast:     (txId: string) => void;
  onShowImage:        (url: string) => void;
  onSpotProposal:     (name: string) => void;
  onSnapResult:       (url: string) => void;
}

// ── GPS 取得 ──
const getGPSLocation = (): Promise<{ lat: number; lng: number } | null> =>
  new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      ()    => resolve(null),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  });

// ── カメラフレームキャプチャ ──
const captureARCameraFrame = (
  containerEl: HTMLDivElement | null
): string | null => {
  const video = containerEl?.querySelector("video");
  if (!video || video.videoWidth === 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.7);
};

// ── Supabase memories バケットへの直接アップロード ──
const uploadMemoryPhoto = async (
  base64DataUrl: string
): Promise<string | null> => {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseKey) return null;

  try {
    const res      = await fetch(base64DataUrl);
    const blob     = await res.blob();
    const fileName = `memory_${Date.now()}.jpg`;

    const uploadRes = await fetch(
      `${supabaseUrl}/storage/v1/object/memories/${fileName}`,
      {
        method: "POST",
        headers: {
          Authorization:   `Bearer ${supabaseKey}`,
          "Content-Type":  "image/jpeg",
          "x-upsert":      "true",
        },
        body: blob,
      }
    );

    if (!uploadRes.ok) {
      console.error("[写真保存] アップロード失敗:", await uploadRes.text());
      return null;
    }

    const imageUrl = `${supabaseUrl}/storage/v1/object/public/memories/${fileName}`;
    console.log("[写真保存] アップロード成功:", imageUrl);
    return imageUrl;
  } catch (err) {
    console.error("[写真保存] エラー:", err);
    return null;
  }
};

export function useChat({
  containerRef,
  inputRef,
  audioInstanceRef,
  timersRef,
  currentEffectRef,
  addressRef,
  address,
  initAudioPipeline,
  playAudio,
  stopAudio,
  onAiStatusChange,
  onSearchPhaseChange,
  onSubtitleChange,
  onSpatialEffect,
  onEngraveToast,
  onShowImage,
  onSpotProposal,
  onSnapResult,
}: UseChatOptions) {
  const [chatHistory,        setChatHistory]        = useState<HistoryItem[]>([]);
  const [isUploadingMemory,  setIsUploadingMemory]  = useState(false);
  const lastGreetingTimeRef  = useRef<number>(0);

  const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

  // ── API レスポンス共通処理 ──
  const handleChatResponse = async (data: ChatApiResponse) => {
    if (data.spatial_effect) {
      onSpatialEffect(data.spatial_effect);
      currentEffectRef.current = data.spatial_effect;
    }
    if (data.spot_proposal)    onSpotProposal(data.spot_proposal);
    if (data.arweave_tx_id)  { onEngraveToast(data.arweave_tx_id); }
    if (data.show_image_url)   onShowImage(data.show_image_url);

    onSubtitleChange(data.reply);

    if (data.audio_data) {
      try {
        await playAudio(data.audio_data, data.audio_mime, () =>
          onAiStatusChange("idle")
        );
        onAiStatusChange("talking");
      } catch {
        onAiStatusChange("talking");
        setTimeout(() => onAiStatusChange("idle"), 5000);
      }
    } else {
      onAiStatusChange("talking");
      setTimeout(() => onAiStatusChange("idle"), 5000);
    }
  };

  // ── ENGRAVE 処理（カメラフレームを保存 → バックエンドに通知） ──
  const handleEngrave = (
    data: ChatApiResponse,
    arweaveId: string
  ) => {
    console.log("[ENGRAVE] engrave_triggered=true");
    const frame = captureARCameraFrame(containerRef.current);
    if (!frame) return;

    setIsUploadingMemory(true);
    uploadMemoryPhoto(frame).then((imageUrl) => {
      setIsUploadingMemory(false);
      if (!imageUrl || !BASE_URL) return;
      fetch(`${BASE_URL}/api/memory/photo`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ arweave_tx_id: arweaveId, image_url: imageUrl }),
      }).catch(console.error);
    });
  };

  // ── 固定挨拶（TTS のみ、LLM を通さない） ──
  const playFixedGreeting = async () => {
    lastGreetingTimeRef.current = Date.now();
    stopAudio();

    const text = "こんにちは、まがときさん。ルキルキ、現実空間への同期完了です。";
    const ts   = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    onSubtitleChange(text);
    onAiStatusChange("talking");
    onSearchPhaseChange("STABLE");
    onSpatialEffect("cyber");
    currentEffectRef.current = "cyber";
    setChatHistory((prev) => [...prev, { role: "ruki", text, timestamp: ts }]);

    try {
      if (!BASE_URL) { onAiStatusChange("idle"); return; }
      const res  = await fetch(`${BASE_URL}/api/tts`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error("TTS生成失敗");
      const { audio_data } = await res.json();
      if (audio_data) {
        await playAudio(audio_data, undefined, () => onAiStatusChange("idle"));
      } else {
        onAiStatusChange("idle");
      }
    } catch {
      setTimeout(() => onAiStatusChange("idle"), 3000);
    }
  };

  // ── 初期挨拶（LLM 経由） ──
  const triggerInitialGreeting = async (
    forcedLocation?: { lat: number; lng: number } | null
  ) => {
    lastGreetingTimeRef.current = Date.now();
    stopAudio();

    onSubtitleChange("ルキルキが現実世界と同期中...");
    onAiStatusChange("thinking");
    onSearchPhaseChange("CONNECTING...");

    const location = forcedLocation !== undefined
      ? forcedLocation
      : await getGPSLocation();
    const imageBase64 = captureARCameraFrame(containerRef.current);

    try {
      const res = await fetch(`${BASE_URL}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message:        "[INITIAL_GREETING]",
          wallet_address: addressRef.current ?? null,
          image_base64:   imageBase64,
          latitude:       location?.lat ?? null,
          longitude:      location?.lng ?? null,
          history:        [],
        }),
      });
      onSearchPhaseChange("STABLE");
      if (!res.ok) throw new Error("API初期挨拶エラー");

      const data: ChatApiResponse = await res.json();
      const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      setChatHistory((prev) => [...prev, { role: "ruki", text: data.reply, timestamp: ts }]);
      await handleChatResponse(data);
    } catch {
      onSubtitleChange("ルキルキを現実世界に固定しました。話しかけてください。");
      onAiStatusChange("idle");
      onSearchPhaseChange("STABLE");
    }
  };

  // ── ターゲット認識コールバック（useAR から呼ばれる） ──
  const onTargetFound = () => {
    const within1Min = (Date.now() - lastGreetingTimeRef.current) < 60_000;
    if (!within1Min) {
      triggerInitialGreeting();
    } else {
      onAiStatusChange("idle");
      onSearchPhaseChange("STABLE");
      onSubtitleChange("ルキルキを現実空間に再同期しました。");
    }
  };

  // ── スナップ生成 ──
  const handleSnap = async (memberName: string) => {
    const cameraImage = captureARCameraFrame(containerRef.current);
    if (!cameraImage) { onSubtitleChange("カメラ映像が取得できませんでした"); return; }

    onAiStatusChange("thinking");
    onSubtitleChange(`📸 ${memberName}とのスナップ写真を生成中...`);

    try {
      const res = await fetch(`${BASE_URL}/api/snap`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          member_name:    memberName,
          camera_image:   cameraImage,
          wallet_address: address ?? null,
        }),
      });
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();

      if (data.status === "ok" && data.image_url) {
        onSnapResult(data.image_url);
        onSubtitleChange(`✨ ${memberName}とのスナップ写真ができたよ！`);
        const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        setChatHistory((prev) => [
          ...prev,
          { role: "user", text: `${memberName}とスナップ`, timestamp: ts },
          { role: "ruki", text: data.message ?? `${memberName}とのスナップ写真ができたよ！`, timestamp: ts },
        ]);
      } else {
        onSubtitleChange(`スナップ生成に失敗しました: ${data.message ?? "不明なエラー"}`);
      }
    } catch (err) {
      console.error("[スナップ] エラー:", err);
      onSubtitleChange("スナップ生成中にエラーが発生しました");
    } finally {
      onAiStatusChange("idle");
    }
  };

  // ── メッセージ送信 ──
  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text     = (formData.get("message") as string).trim();
    if (!text) return;

    // スナップコマンド検出
    const snapMatch = text.match(/^(.+?)とスナップ$/);
    if (snapMatch) {
      if (inputRef.current) inputRef.current.value = "";
      await handleSnap(snapMatch[1].trim());
      return;
    }

    if (inputRef.current) inputRef.current.blur();
    setTimeout(() => window.scrollTo({ top: 0, behavior: "smooth" }), 100);

    stopAudio();

    onSubtitleChange(`思考中... 「${text}」`);
    onAiStatusChange("thinking");
    onSearchPhaseChange("CONNECTING...");

    const ts             = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const updatedHistory = [...chatHistory, { role: "user" as const, text, timestamp: ts }];
    setChatHistory(updatedHistory);

    timersRef.current.push(
      setTimeout(() => {
        onSearchPhaseChange("TAVILY_SEARCHING...");
        onSubtitleChange("🌐 外部情報空間を走査中...\n（Tavilyサーチを同期しています）");
      }, 1800),
      setTimeout(() => {
        onSearchPhaseChange("DATA_ANALYZING...");
        onSubtitleChange("🔮 取得した時間軸データを展開中...\n（ルキルキが回答を再構成しています）");
      }, 5000)
    );

    const location    = await getGPSLocation();
    const imageBase64 = captureARCameraFrame(containerRef.current);

    try {
      const res = await fetch(`${BASE_URL}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message:        text,
          wallet_address: address ?? null,
          image_base64:   imageBase64,
          latitude:       location?.lat ?? null,
          longitude:      location?.lng ?? null,
          history:        updatedHistory,
        }),
      });

      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
      onSearchPhaseChange("STABLE");
      if (!res.ok) throw new Error("API接続エラー");

      const data: ChatApiResponse = await res.json();
      if (inputRef.current) inputRef.current.value = "";

      // ENGRAVE 処理
      console.log("[ENGRAVE] data.engrave_triggered=", data.engrave_triggered);
      if (data.engrave_triggered) handleEngrave(data, data.arweave_tx_id);

      setChatHistory((prev) => [...prev, { role: "ruki", text: data.reply, timestamp: ts }]);
      await handleChatResponse(data);
    } catch {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
      onSearchPhaseChange("OFFLINE");
      onSubtitleChange("バックエンドとの通信に失敗しました。");
      onAiStatusChange("idle");
    }
  };

  return {
    chatHistory,
    isUploadingMemory,
    handleSendMessage,
    onTargetFound,
    triggerInitialGreeting,
    playFixedGreeting,
    captureARCameraFrame: () => captureARCameraFrame(containerRef.current),
  };
}
