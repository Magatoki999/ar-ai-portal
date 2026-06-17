// MindARViewer.tsx
// ─────────────────────────────────────────────────────────────────────────────
// ARビューアのトップレベルコンポーネント。
// 責務はフックの組み合わせと state の保持のみ。
// ロジックは hooks/* / components/* に完全委譲している。
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useRef, useState, useCallback } from "react";
import type { AIStatus, SearchPhase } from "./lib/types";

import { useVoice }      from "./hooks/useVoice";
import { useWebSocket }  from "./hooks/useWebSocket";
import { useAR }         from "./hooks/useAR";
import { useChat }       from "./hooks/useChat";

import { RukiHUD }       from "./components/RukiHUD";
import { HistoryPanel }  from "./components/HistoryPanel";
import { SnapViewer }    from "./components/SnapViewer";

interface MindARViewerProps {
  address?: string;
}

export default function MindARViewer({ address }: MindARViewerProps) {
  // ── DOM refs ──
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef     = useRef<HTMLInputElement>(null);
  const timersRef    = useRef<NodeJS.Timeout[]>([]);
  const addressRef   = useRef<string | undefined>(address);

  // ── エフェクト同期 ref（レンダリングループと共有） ──
  const currentEffectRef = useRef<string>("cyber");

  // ── UI ステート ──
  const [aiStatus,         setAiStatus]         = useState<AIStatus>("idle");
  const [searchPhase,      setSearchPhase]      = useState<SearchPhase>("OFFLINE");
  const [subtitle,         setSubtitle]         = useState<string>("");
  const [spatialEffect,    setSpatialEffect]    = useState<string>("cyber");
  const [showHistory,      setShowHistory]      = useState<boolean>(false);
  const [snapImageUrl,     setSnapImageUrl]     = useState<string | null>(null);
  const [showImageUrl,     setShowImageUrl]     = useState<string | null>(null);
  const [engraveToastTxId, setEngraveToastTxId] = useState<string | null>(null);
  const [spotProposal,     setSpotProposal]     = useState<string | null>(null);

  // ── ENGRAVE トースト（5秒後に自動消去） ──
  const handleEngraveToast = useCallback((txId: string) => {
    setEngraveToastTxId(txId);
    setTimeout(() => setEngraveToastTxId(null), 5000);
  }, []);

  // ── スポット提案（10秒後に自動消去） ──
  const handleSpotProposal = useCallback((name: string) => {
    setSpotProposal(name);
    setTimeout(() => setSpotProposal(null), 10_000);
  }, []);

  // ── 1. 音声フック ──
  const {
    isListening,
    toggleListening,
    initAudioPipeline,
    playAudio,
    stopAudio,
    updateMouthMorph,
    audioInstanceRef,
    audioContextRef,
    mouthTargetsRef,
    blinkTargetsRef,
  } = useVoice({
    inputRef,
    timersRef,
    onAiStatusChange: setAiStatus,
    onTranscript: (text) => {
      if (inputRef.current) {
        inputRef.current.value = text;
        const form = inputRef.current.closest("form");
        if (form) form.requestSubmit();
      }
    },
  });

  // ── 2. チャットフック ──
  const {
    chatHistory,
    isUploadingMemory,
    handleSendMessage,
    onTargetFound: chatOnTargetFound,
    captureARCameraFrame,
  } = useChat({
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
    onAiStatusChange:    setAiStatus,
    onSearchPhaseChange: setSearchPhase,
    onSubtitleChange:    setSubtitle,
    onSpatialEffect: (effect) => {
      setSpatialEffect(effect);
      currentEffectRef.current = effect;
    },
    onEngraveToast:  handleEngraveToast,
    onShowImage:     (url) => setShowImageUrl(url),
    onSpotProposal:  handleSpotProposal,
    onSnapResult:    (url) => setSnapImageUrl(url),
  });

  // ── 3. WebSocket フック ──
  const { notifyTargetFound, notifyTargetLost } = useWebSocket({
    audioInstanceRef,
    audioContextRef,
    timersRef,
    initAudioPipeline,
    onProactiveSpeech: (text, effect) => {
      setSubtitle(text);
      setSpatialEffect(effect);
      currentEffectRef.current = effect;
    },
    onAiStatusChange: setAiStatus,
  });

  // ── 4. AR フック ──
  const { fadeToAction } = useAR({
    containerRef,
    currentEffectRef,
    mouthTargetsRef,
    blinkTargetsRef,
    updateMouthMorph,
    onTargetFound: () => {
      notifyTargetFound();
      chatOnTargetFound();
    },
    onTargetLost: () => {
      notifyTargetLost();
      setSubtitle("（通信継続中... マーカーから目を離してもそのまま話しかけられます）");
    },
    onSubtitleChange: setSubtitle,
    onStatusChange:   setAiStatus,
  });

  // ── AI ステータス変化時のアニメーション連携 ──
  // useEffect で aiStatus を監視してクロスフェード
  // （useAR が fadeToAction を返すため、外側で監視する）
  const prevAiStatus = useRef<AIStatus>("idle");
  if (prevAiStatus.current !== aiStatus) {
    prevAiStatus.current = aiStatus;
    fadeToAction(aiStatus);
  }

  // ── 記憶写真撮影（カメラフレーム → Supabase 保存） ──
  const handleCaptureSave = async () => {
    const frame = captureARCameraFrame();
    if (!frame) { setSubtitle("カメラ映像が取得できませんでした"); return; }

    setSubtitle("📷 記憶写真を撮影・保存中...");
    setIsCapturing(true);
    try {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
      const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
      if (!supabaseUrl || !supabaseKey) throw new Error("Supabase設定なし");

      const res     = await fetch(frame);
      const blob    = await res.blob();
      const ts      = Date.now();
      const fileName = `memory_${ts}.jpg`;

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
      if (!uploadRes.ok) throw new Error("アップロード失敗");

      const imageUrl = `${supabaseUrl}/storage/v1/object/public/memories/${fileName}`;
      const baseUrl  = process.env.NEXT_PUBLIC_API_URL;
      if (baseUrl) {
        await fetch(`${baseUrl}/api/save_memory_image`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ wallet_address: address, image_url: imageUrl }),
        });
      }
      setSubtitle("📷 記憶写真を保存しました！");
      setSnapImageUrl(imageUrl);
    } catch (err) {
      console.error("[写真保存]", err);
      setSubtitle("写真の保存に失敗しました");
    } finally {
      setIsCapturing(false);
    }
  };

  const [isCapturing, setIsCapturing] = useState(false);

  // ─────────────────────────────────────────────────────────────────────────
  // レンダリング
  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="fixed inset-0 w-full h-full overflow-hidden bg-black">
      {/* AR コンテナ（MindAR が video / canvas をここに注入する） */}
      <div ref={containerRef} className="absolute inset-0 w-full h-full" />

      {/* HUD オーバーレイ */}
      <RukiHUD
        aiStatus={aiStatus}
        searchPhase={searchPhase}
        subtitle={subtitle}
        isListening={isListening}
        isUploadingMemory={isUploadingMemory || isCapturing}
        engraveToastTxId={engraveToastTxId}
        spotProposal={spotProposal}
        onToggleListen={toggleListening}
        onOpenLog={() => setShowHistory(true)}
        onSendMessage={handleSendMessage}
        onCaptureSave={handleCaptureSave}
      />

      {/* ミッションログパネル */}
      {showHistory && (
        <HistoryPanel
          history={chatHistory}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* スナップ / 思い出写真ビューア */}
      {(snapImageUrl || showImageUrl) && (
        <SnapViewer
          imageUrl={(snapImageUrl || showImageUrl)!}
          onClose={() => { setSnapImageUrl(null); setShowImageUrl(null); }}
        />
      )}
    </div>
  );
}
