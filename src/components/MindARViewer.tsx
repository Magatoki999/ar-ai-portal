// MindARViewer.tsx
// ─────────────────────────────────────────────────────────────────────────────
// ARビューアのトップレベルコンポーネント。
// 責務はフックの組み合わせと state の保持のみ。
// ロジックは hooks/* / components/* に完全委譲している。
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import type { AIStatus, SearchPhase, FacialEmotion } from "./lib/types";

import { useVoice }      from "./hooks/useVoice";
import { useWebSocket }  from "./hooks/useWebSocket";
import { useAR }         from "./hooks/useAR";
import { useChat, getGPSLocation } from "./hooks/useChat";

import { RukiHUD }       from "./components/RukiHUD";
import { HistoryPanel }  from "./components/HistoryPanel";
import { SnapViewer }    from "./components/SnapViewer";
import { VideoViewer }   from "./components/VideoViewer";
import { BookScanModal } from "./components/BookScanModal";

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

  // マーカーロスト時、直前のセリフをすぐ消さず少し見せておくための遅延タイマー。
  // 連続でロスト/再認識が起きた場合に前のタイマーが古い字幕で上書きしないよう、
  // refで保持して都度クリアする。
  const lostSubtitleTimerRef = useRef<NodeJS.Timeout | null>(null);

  // ── UI ステート ──
  const [aiStatus,         setAiStatus]         = useState<AIStatus>("idle");
  const [searchPhase,      setSearchPhase]      = useState<SearchPhase>("OFFLINE");
  const [subtitle,         setSubtitle]         = useState<string>("");
  const [spatialEffect,    setSpatialEffect]    = useState<string>("cyber");
  const [showHistory,      setShowHistory]      = useState<boolean>(false);
  const [snapImageUrl,     setSnapImageUrl]     = useState<string | null>(null);
  const [showImageUrl,     setShowImageUrl]     = useState<string | null>(null);
  const [showVideoUrl,     setShowVideoUrl]     = useState<string | null>(null);
  const [engraveToastTxId, setEngraveToastTxId] = useState<string | null>(null);
  const [spotProposal,     setSpotProposal]     = useState<string | null>(null);

  const [isCapturing,      setIsCapturing]      = useState(false);

  // 読書通帳：バーコードスキャンモーダルの開閉。isTargetLost中のみ📔ボタンが出るため、
  // モーダル自体もisTargetLost中にしか開かれない想定だが、念のため独立したstateにしている。
  const [showBookScan,     setShowBookScan]     = useState(false);

  // マーカーロスト中かどうか。RukiFaceIcon（右下の顔アイコン）の表示切り替えに使う。
  // 字幕の15秒保持（lostSubtitleTimerRef）とは独立しており、アイコンはロスト中ずっと表示し続ける。
  const [isTargetLost, setIsTargetLost] = useState(false);

  // RukiFaceIcon の表情（fun/sad/worry/angry/neutral）。evaluator_node がセリフの意味合いから
  // 分類した結果をAPIレスポンスから受け取る。aiStatusが"talking"のときだけ参照される。
  const [facialEmotion, setFacialEmotion] = useState<FacialEmotion>("neutral");

  // setSubtitle のラッパー。新しい字幕がセットされる時点で、ロスト後に仕込んだ
  // 「5秒後にプレースホルダーへ戻す」タイマーが残っていれば必ずキャンセルする。
  // これによりロスト中でもテキストで会話を続けた場合、その返答がタイマーで
  // 後から上書きされる事故を防ぐ。
  const updateSubtitle = useCallback((text: string) => {
    if (lostSubtitleTimerRef.current) {
      clearTimeout(lostSubtitleTimerRef.current);
      lostSubtitleTimerRef.current = null;
    }
    setSubtitle(text);
  }, []);

  // アンマウント時、保留中のロスト字幕タイマーが残らないようクリア
  useEffect(() => {
    return () => {
      if (lostSubtitleTimerRef.current) {
        clearTimeout(lostSubtitleTimerRef.current);
        lostSubtitleTimerRef.current = null;
      }
    };
  }, []);

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
      if (!inputRef.current) return;
      // React の onChange を経由せず value を直接書き込み、
      // nativeInputValueSetter でイベントを発火させてから submit する
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value"
      )?.set;
      nativeSetter?.call(inputRef.current, text);
      inputRef.current.dispatchEvent(new Event("input", { bubbles: true }));
      // フォームを探して送信
      const form = inputRef.current.closest("form");
      if (form) {
        form.requestSubmit();
      }
    },
  });

  // ── 2. WebSocket フック ──
  // 割り込み機能で useChat が sendInterrupt を必要とするため、useChatより先に呼ぶ。
  const { notifyTargetFound, notifyTargetLost, sendInterrupt, sendLocationUpdate } = useWebSocket({
    audioInstanceRef,
    audioContextRef,
    timersRef,
    initAudioPipeline,
    onProactiveSpeech: (text, effect) => {
      updateSubtitle(text);
      setSpatialEffect(effect);
      currentEffectRef.current = effect;
    },
    onAiStatusChange: setAiStatus,
  });

  // ── 3. チャットフック ──
  const {
    chatHistory,
    isUploadingMemory,
    handleSendMessage,
    onTargetFound: chatOnTargetFound,
    resetBusy,
    interrupt,
    isBusyRef,
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
    onSubtitleChange:    updateSubtitle,
    onSpatialEffect: (effect) => {
      setSpatialEffect(effect);
      currentEffectRef.current = effect;
    },
    onEngraveToast:  handleEngraveToast,
    onShowImage:     (url) => setShowImageUrl(url),
    onShowVideo:     (url) => setShowVideoUrl(url),
    onSpotProposal:  handleSpotProposal,
    onSnapResult:    (url) => setSnapImageUrl(url),
    onFacialEmotionChange: setFacialEmotion,
    sendInterrupt,
  });

  // ── 現在地の定期送信 ──
  // spot_proximity_job（登録スポット接近検知）・weather_prep_jobが「最後に分かった
  // 現在地」として参照できるよう、会話の有無に関わらず1分おきにGPSを送信しておく。
  // useChat.tsの/api/chat呼び出し時のGPS取得（getGPSLocation）と同じロジックを再利用。
  useEffect(() => {
    const ping = async () => {
      const location = await getGPSLocation();
      if (location) {
        sendLocationUpdate(location.lat, location.lng);
      }
    };
    ping(); // 起動直後に1回
    const intervalId = setInterval(ping, 60_000);
    return () => clearInterval(intervalId);
  }, [sendLocationUpdate]);

  // ── マイクボタン用ラッパー ──
  // 応答生成中/再生中にもう一度マイクボタンが押された場合は、
  // 「言い間違えた・もう一回話したい」という割り込みの意思表示として扱い、
  // 新しい録音を始める前にinterrupt()でバックエンドのタスクをキャンセルする。
  // （useVoice.ts自体はisBusyRef/interruptを知らないシンプルな作りのままにし、
  //   ここで吸収することでuseVoice→useChatの循環参照を避けている）
  const handleToggleListen = useCallback(() => {
    if (isBusyRef.current) {
      // キャンセルしてスタンバイに戻すだけ。録音の再開はここでは行わない
      // （言い間違えた場合など、いったん考え直す間を与えるため。
      //   録音を始めたい場合はユーザーがもう一度ボタンを押す）
      interrupt();
      return;
    }
    toggleListening();
  }, [interrupt, isBusyRef, toggleListening]);

  // ── 4. AR フック ──
  const { fadeToAction } = useAR({
    containerRef,
    currentEffectRef,
    mouthTargetsRef,
    blinkTargetsRef,
    updateMouthMorph,
    onTargetFound: () => {
      // 再認識時、ロストで仕込んだ「字幕を5秒後にプレースホルダーへ戻す」タイマーが
      // 残っていると、新しい会話の字幕を後から上書きしてしまうため先にキャンセルする。
      if (lostSubtitleTimerRef.current) {
        clearTimeout(lostSubtitleTimerRef.current);
        lostSubtitleTimerRef.current = null;
      }
      setIsTargetLost(false);
      notifyTargetFound();
      chatOnTargetFound();
    },
    onTargetLost: () => {
      notifyTargetLost();
      // busy / thinking 状態を強制解除してロスト後も会話できるようにする
      resetBusy();
      setAiStatus("idle");
      setIsTargetLost(true);

      // ルキルキが話していたセリフ（字幕）はすぐに消さず、15秒間そのまま見せておく
      // （以前は5秒だったが、歩きながらの利用でマーカー認識が頻繁に途切れると
      // 読み終わる前に消えてしまうとの指摘を受けて延長、2026-08-15）。
      // それまでに別の理由で字幕が更新されていれば、このタイマーは古い文言で
      // 上書きしないようにキャンセルする。
      if (lostSubtitleTimerRef.current) {
        clearTimeout(lostSubtitleTimerRef.current);
      }
      lostSubtitleTimerRef.current = setTimeout(() => {
        setSubtitle("（マーカーをかざしてください。話しかけることもできます）");
        lostSubtitleTimerRef.current = null;
      }, 15000);
    },
    onSubtitleChange: updateSubtitle,
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
    if (!frame) { updateSubtitle("カメラ映像が取得できませんでした"); return; }

    updateSubtitle("📷 記憶写真を撮影・保存中...");
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
        // 直前のユーザー発話を一緒に送る。バックエンド側で食事の発話かどうかを判定し、
        // 「これ食べてる」のように話しながら撮った場合は meal_logs にも自動で紐付ける
        // （孤食ロボット機能：食事の様子を写真付きで記録できるようにするため）。
        const lastUserMsg = [...chatHistory].reverse().find((h) => h.role === "user");
        await fetch(`${baseUrl}/api/save_memory_image`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            wallet_address: address,
            image_url: imageUrl,
            recent_user_text: lastUserMsg?.text ?? "",
          }),
        });
      }
      updateSubtitle("📷 記憶写真を保存しました！");
      setSnapImageUrl(imageUrl);
    } catch (err) {
      console.error("[写真保存]", err);
      updateSubtitle("写真の保存に失敗しました");
    } finally {
      setIsCapturing(false);
    }
  };

  // ─────────────────────────────────────────────────────────────────────────
  // レンダリング
  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: "fixed", top: 0, left: 0, width: "100%", height: "100%", overflow: "hidden", background: "#000" }}>
      {/* AR コンテナ（MindAR が video / canvas をここに注入する） */}
      {/* ⚠️ 100vw はスクロールバー幅を含むためNG。100% で親に追従させる。 */}
      {/* useAR.ts の start() 後に position:fixed などを上書き適用する。 */}
      <div ref={containerRef} style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", overflow: "hidden" }} />

      {/* HUD オーバーレイ */}
      <RukiHUD
        aiStatus={aiStatus}
        searchPhase={searchPhase}
        subtitle={subtitle}
        isListening={isListening}
        isUploadingMemory={isUploadingMemory || isCapturing}
        engraveToastTxId={engraveToastTxId}
        spotProposal={spotProposal}
        onToggleListen={handleToggleListen}
        onOpenLog={() => setShowHistory(true)}
        onSendMessage={handleSendMessage}
        onCaptureSave={handleCaptureSave}
        inputRef={inputRef}
        isTargetLost={isTargetLost}
        facialEmotion={facialEmotion}
        onOpenBookScan={() => setShowBookScan(true)}
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

      {/* AI動画生成結果ビューア */}
      {showVideoUrl && (
        <VideoViewer
          videoUrl={showVideoUrl}
          onClose={() => setShowVideoUrl(null)}
        />
      )}

      {/* 読書通帳：バーコードスキャンモーダル（顔アイコン表示中のみ📔ボタンから開く） */}
      {showBookScan && (
        <BookScanModal
          containerRef={containerRef}
          onClose={() => setShowBookScan(false)}
          onLogged={(book) => {
            updateSubtitle(`📔「${book.title}」を記帳しました！`);
          }}
        />
      )}
    </div>
  );
}
