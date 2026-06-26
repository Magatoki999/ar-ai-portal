// hooks/useChat.ts
"use client";

import { useState, useRef, useCallback } from "react";
import type {
  AIStatus,
  HistoryItem,
  SearchPhase,
  ChatApiResponse,
  FacialEmotion,
} from "../lib/types";
import { base64ToAudioUrl, resolveAudioMime } from "../lib/audio";

interface UseChatOptions {
  containerRef:        React.MutableRefObject<HTMLDivElement | null>;
  inputRef:            React.MutableRefObject<HTMLInputElement | null>;
  audioInstanceRef:    React.MutableRefObject<HTMLAudioElement | null>;
  timersRef:           React.MutableRefObject<NodeJS.Timeout[]>;
  currentEffectRef:    React.MutableRefObject<string>;
  addressRef:          React.MutableRefObject<string | undefined>;
  address?:            string;
  initAudioPipeline:   (audio: HTMLAudioElement) => void;
  playAudio:           (b64: string, mime?: string, onEnded?: () => void) => Promise<void>;
  stopAudio:           () => void;
  onAiStatusChange:    (status: AIStatus) => void;
  onSearchPhaseChange: (phase: SearchPhase) => void;
  onSubtitleChange:    (text: string) => void;
  onSpatialEffect:     (effect: string) => void;
  onEngraveToast:      (txId: string) => void;
  onShowImage:         (url: string) => void;
  onSpotProposal:      (name: string) => void;
  onSnapResult:        (url: string) => void;
  // RukiFaceIcon（マーカーロスト中の顔アイコン）の表情切替用。2026-06-26追加。
  onFacialEmotionChange?: (emotion: FacialEmotion) => void;
}

// ── GPS 取得 ──
const getGPSLocation = (): Promise<{ lat: number; lng: number } | null> =>
  new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      ()    => resolve(null),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 }
    );
  });

// ── カメラフレームキャプチャ ──
const captureFrame = (containerEl: HTMLDivElement | null): string | null => {
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

// ── Supabase memories バケットへのアップロード ──
const uploadMemoryPhoto = async (base64DataUrl: string): Promise<string | null> => {
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
        method:  "POST",
        headers: {
          Authorization:  `Bearer ${supabaseKey}`,
          "Content-Type": "image/jpeg",
          "x-upsert":     "true",
        },
        body: blob,
      }
    );
    if (!uploadRes.ok) return null;
    return `${supabaseUrl}/storage/v1/object/public/memories/${fileName}`;
  } catch { return null; }
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
  onFacialEmotionChange,
}: UseChatOptions) {
  const [chatHistory,       setChatHistory]       = useState<HistoryItem[]>([]);
  const [isUploadingMemory, setIsUploadingMemory] = useState(false);

  // ── 状態管理 ref（レンダリングに依存しない制御フラグ） ──
  const isBusyRef            = useRef<boolean>(false); // API呼び出し中フラグ
  const lastGreetingTimeRef  = useRef<number>(0);
  const chatHistoryRef       = useRef<HistoryItem[]>([]); // chatHistory の最新値を ref でも保持

  const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

  // chatHistory の更新を ref にも反映
  const updateHistory = useCallback((updater: (prev: HistoryItem[]) => HistoryItem[]) => {
    setChatHistory((prev) => {
      const next = updater(prev);
      chatHistoryRef.current = next;
      return next;
    });
  }, []);

  // ── 音声再生（非ブロッキング） ──
  // await しない。onEnded コールバックで idle に戻す。
  const playReply = useCallback((
    audioB64: string | null,
    mime?: string,
    onDone?: () => void
  ) => {
    if (!audioB64 || !audioInstanceRef.current) {
      // 音声なし: 5秒後に idle
      onAiStatusChange("talking");
      const t = setTimeout(() => { onAiStatusChange("idle"); onDone?.(); }, 5000);
      timersRef.current.push(t);
      return;
    }
    const resolvedMime = resolveAudioMime(mime);
    const url = base64ToAudioUrl(audioB64, resolvedMime);
    const audio = audioInstanceRef.current;

    // 再生前に前の音声を確実に止める
    audio.pause();
    audio.onended = null;

    audio.onended = () => {
      URL.revokeObjectURL(url);
      onAiStatusChange("idle");
      onDone?.();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      onAiStatusChange("idle");
      onDone?.();
    };

    try {
      if (initAudioPipeline) initAudioPipeline(audio);
      audio.src = url;
      onAiStatusChange("talking");
      audio.play().catch(() => {
        URL.revokeObjectURL(url);
        onAiStatusChange("idle");
        onDone?.();
      });
    } catch {
      URL.revokeObjectURL(url);
      onAiStatusChange("idle");
      onDone?.();
    }
  }, [audioInstanceRef, initAudioPipeline, onAiStatusChange, timersRef]);

  // ── API レスポンス共通処理（非ブロッキング版） ──
  const applyResponse = useCallback((data: ChatApiResponse) => {
    if (data.spatial_effect) {
      onSpatialEffect(data.spatial_effect);
      currentEffectRef.current = data.spatial_effect;
    }
    if (data.spot_proposal)  onSpotProposal(data.spot_proposal);
    if (data.arweave_tx_id)  onEngraveToast(data.arweave_tx_id);
    if (data.show_image_url) onShowImage(data.show_image_url);
    // RukiFaceIcon の表情切替（talking 中のみ反映される。idle/thinking 中は無視されるため、
    // 値が来ても安全に渡せる）。
    console.log("[useChat] data.facial_emotion=", data.facial_emotion, "onFacialEmotionChange存在=", !!onFacialEmotionChange);
    if (data.facial_emotion) onFacialEmotionChange?.(data.facial_emotion);

    onSubtitleChange(data.reply);
    // 非ブロッキングで再生開始。完了後に isBusy を解除
    playReply(data.audio_data, data.audio_mime, () => {
      isBusyRef.current = false;
    });
  }, [onSpatialEffect, onSpotProposal, onEngraveToast, onShowImage, onFacialEmotionChange,
      onSubtitleChange, playReply, currentEffectRef]);

  // ── ENGRAVE 処理 ──
  const handleEngrave = useCallback((arweaveId: string) => {
    const frame = captureFrame(containerRef.current);
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
  }, [containerRef, BASE_URL]);

  // ── /api/chat 呼び出しコア ──
  const callChat = useCallback(async (
    message: string,
    history: HistoryItem[],
    opts?: { isGreeting?: boolean }
  ): Promise<void> => {
    if (isBusyRef.current) {
      console.log("[useChat] busy中のため送信スキップ:", message.slice(0, 20));
      return;
    }
    isBusyRef.current = true;

    onAiStatusChange("thinking");
    onSearchPhaseChange("CONNECTING...");

    // Tavily 検索中インジケーター（1.8秒後・5秒後）
    const t1 = setTimeout(() => {
      onSearchPhaseChange("TAVILY_SEARCHING...");
      onSubtitleChange("🌐 外部情報空間を走査中...");
    }, 1800);
    const t2 = setTimeout(() => {
      onSearchPhaseChange("DATA_ANALYZING...");
      onSubtitleChange("🔮 データを展開中...");
    }, 5000);
    timersRef.current.push(t1, t2);

    const location    = await getGPSLocation();
    const imageBase64 = captureFrame(containerRef.current);

    try {
      const res = await fetch(`${BASE_URL}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          wallet_address: addressRef.current ?? null,
          image_base64:   imageBase64,
          latitude:       location?.lat ?? null,
          longitude:      location?.lng ?? null,
          history:        opts?.isGreeting ? [] : history,
        }),
      });

      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
      onSearchPhaseChange("STABLE");

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data: ChatApiResponse = await res.json();

      // 履歴にルキルキの返答を追記
      const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      updateHistory((prev) => [...prev, { role: "ruki", text: data.reply, timestamp: ts }]);

      // ENGRAVE
      if (data.engrave_triggered) handleEngrave(data.arweave_tx_id);

      // レスポンス適用（音声再生は非ブロッキング・isBusy は playReply 完了後に解除）
      applyResponse(data);

    } catch (err) {
      console.error("[useChat] API呼び出しエラー:", err);
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
      onSearchPhaseChange("OFFLINE");
      onSubtitleChange("通信に失敗しました。もう一度話しかけてください。");
      onAiStatusChange("idle");
      isBusyRef.current = false;
    }
  }, [BASE_URL, addressRef, containerRef, timersRef,
      onAiStatusChange, onSearchPhaseChange, onSubtitleChange,
      updateHistory, handleEngrave, applyResponse]);

  // ── 初期挨拶（マーカー認識時） ──
  // 固定wavによる「場をつなぐ」処理は効果が薄く、処理が増えるだけだったため削除。
  // シンプにAPIへ直接[INITIAL_GREETING]を投げる。
  const triggerInitialGreeting = useCallback(async () => {
    lastGreetingTimeRef.current = Date.now();
    stopAudio();
    onSubtitleChange("ルキルキが現実世界と同期中...");
    await callChat("[INITIAL_GREETING]", [], { isGreeting: true });
  }, [callChat, stopAudio, onSubtitleChange]);

  // ── ターゲット認識コールバック ──
  const onTargetFound = useCallback(() => {
    const elapsed = Date.now() - lastGreetingTimeRef.current;
    const within5Min = elapsed < 5 * 60_000;

    if (!within5Min || lastGreetingTimeRef.current === 0) {
      // 初回 or 5分超 → 挨拶を送る（busy中なら自然にスキップ）
      triggerInitialGreeting();
    } else {
      // 5分以内の再認識 → idle に戻すだけ
      onAiStatusChange("idle");
      onSearchPhaseChange("STABLE");
      onSubtitleChange("話しかけてください。");
    }
  }, [triggerInitialGreeting, onAiStatusChange, onSearchPhaseChange, onSubtitleChange]);

  // ── スナップ生成 ──
  const handleSnap = useCallback(async (memberName: string) => {
    if (isBusyRef.current) return;
    isBusyRef.current = true;
    const cameraImage = captureFrame(containerRef.current);
    if (!cameraImage) {
      onSubtitleChange("カメラ映像が取得できませんでした");
      isBusyRef.current = false;
      return;
    }
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
        updateHistory((prev) => [
          ...prev,
          { role: "user", text: `${memberName}とスナップ`, timestamp: ts },
          { role: "ruki", text: data.message ?? `${memberName}とのスナップ写真ができたよ！`, timestamp: ts },
        ]);
      } else {
        onSubtitleChange(`スナップ生成に失敗: ${data.message ?? "不明なエラー"}`);
      }
    } catch (err) {
      console.error("[スナップ]", err);
      onSubtitleChange("スナップ生成中にエラーが発生しました");
    } finally {
      onAiStatusChange("idle");
      isBusyRef.current = false;
    }
  }, [BASE_URL, address, containerRef, onAiStatusChange, onSubtitleChange,
      onSnapResult, updateHistory]);

  // ── メッセージ送信 ──
  const handleSendMessage = useCallback(async (
    e: React.FormEvent<HTMLFormElement>
  ) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text     = (formData.get("message") as string).trim();
    if (!text) return;

    // スナップコマンド
    const snapMatch = text.match(/^(.+?)とスナップ$/);
    if (snapMatch) {
      if (inputRef.current) inputRef.current.value = "";
      await handleSnap(snapMatch[1].trim());
      return;
    }

    if (inputRef.current) {
      inputRef.current.value = "";
      inputRef.current.blur();
    }

    // busy中は字幕でフィードバックして早期リターン
    if (isBusyRef.current) {
      onSubtitleChange("少し待ってください...");
      return;
    }

    const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    // 送信前に履歴へユーザー発言を追加
    const newHistory = [...chatHistoryRef.current, { role: "user" as const, text, timestamp: ts }];
    chatHistoryRef.current = newHistory;
    setChatHistory(newHistory);

    onSubtitleChange(`「${text}」`);

    await callChat(text, newHistory);
  }, [callChat, handleSnap, inputRef, onSubtitleChange]);

  // ── busy 強制解除（ロスト時などに外から呼ぶ） ──
  const resetBusy = useCallback(() => {
    isBusyRef.current = false;
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
    // 音声を止めてonEndedコールバックも無効化（古いコールバックの干渉を防ぐ）
    if (audioInstanceRef.current) {
      audioInstanceRef.current.pause();
      audioInstanceRef.current.onended = null;
      audioInstanceRef.current.onerror = null;
      audioInstanceRef.current.src = "";
    }
  }, [timersRef, audioInstanceRef]);

  return {
    chatHistory,
    isUploadingMemory,
    handleSendMessage,
    onTargetFound,
    triggerInitialGreeting,
    resetBusy,
    // 後方互換
    playFixedGreeting: triggerInitialGreeting,
    captureARCameraFrame: () => captureFrame(containerRef.current),
  };
}
