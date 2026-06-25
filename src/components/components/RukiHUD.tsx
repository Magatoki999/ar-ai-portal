// components/RukiHUD.tsx
"use client";

import type { AIStatus, SearchPhase } from "../lib/types";
import { RukiFaceIcon } from "./RukiFaceIcon";

interface RukiHUDProps {
  aiStatus:          AIStatus;
  searchPhase:       SearchPhase;
  subtitle:          string;
  isListening:       boolean;
  isUploadingMemory: boolean;
  engraveToastTxId:  string | null;
  spotProposal:      string | null;
  onToggleListen:    () => void;
  onOpenLog:         () => void;
  onSendMessage:     (e: React.FormEvent<HTMLFormElement>) => void;
  onCaptureSave:     () => void;
  inputRef:          React.RefObject<HTMLInputElement>;
  isGpsLoading?:     boolean;
  // マーカーロスト中かどうか。true の間、右下に顔アイコン（RukiFaceIcon）を表示する。
  // ロスト中も会話自体は継続できるため、ルキルキの存在感が薄くならないようにするための表示。
  isTargetLost?:     boolean;
}

export function RukiHUD({
  aiStatus,
  searchPhase,
  subtitle,
  isListening,
  isUploadingMemory,
  engraveToastTxId,
  spotProposal,
  onToggleListen,
  onOpenLog,
  onSendMessage,
  onCaptureSave,
  inputRef,
  isTargetLost = false,
}: RukiHUDProps) {
  const phaseColor =
    searchPhase === "STABLE"  ? "text-emerald-400" :
    searchPhase === "OFFLINE" ? "text-red-400"     : "text-yellow-400";
  const statusIcon  = aiStatus === "thinking" ? "🔮" : aiStatus === "talking" ? "🗣️" : "💤";
  const statusLabel = aiStatus === "thinking" ? "思考中" : aiStatus === "talking" ? "発話中" : "スタンバイ";

  return (
    // 最外コンテナ: fixed で全画面を覆う。pointer-events-none でAR操作を妨げない
    <div className="fixed inset-0 flex flex-col" style={{ zIndex: 100, pointerEvents: "none" }}>

      {/* ステータスバー（タップ透過） */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <div className="flex items-center gap-2 bg-black/50 backdrop-blur-sm rounded-full px-3 py-1 border border-white/10">
          <span className="text-[10px] font-mono text-purple-400 tracking-widest">RUKI_SYNC</span>
          <span className={`text-[10px] font-mono ${phaseColor}`}>{searchPhase}</span>
        </div>
        <div className="flex items-center gap-1.5 bg-black/50 backdrop-blur-sm rounded-full px-3 py-1 border border-white/10">
          <span className="text-xs">{statusIcon}</span>
          <span className="text-[10px] font-mono text-gray-300">{statusLabel}</span>
        </div>
      </div>

      {/* トースト類（タップ透過） */}
      {engraveToastTxId && (
        <div className="mx-4 mt-2">
          <div className="bg-amber-900/80 border border-amber-400/50 rounded-xl px-4 py-2.5 text-xs font-mono text-amber-200 flex items-center gap-2 shadow-lg">
            <span>⛓️</span>
            <div className="flex flex-col">
              <span className="font-bold text-amber-300">記憶をArweaveに永久刻印しました</span>
              <span className="text-[9px] text-amber-400/80 break-all">TX: {engraveToastTxId.slice(0, 20)}...</span>
            </div>
          </div>
        </div>
      )}
      {spotProposal && (
        <div className="mx-4 mt-2">
          <div className="bg-teal-900/80 border border-teal-400/40 rounded-xl px-4 py-2.5 text-xs font-mono text-teal-200 flex items-center gap-2">
            <span>📍</span>
            <span><span className="font-bold text-teal-300">{spotProposal}</span>の近くにいます。記憶を刻みますか？</span>
          </div>
        </div>
      )}
      {isUploadingMemory && (
        <div className="mx-4 mt-2">
          <div className="bg-purple-900/80 border border-purple-400/40 rounded-xl px-4 py-2.5 text-xs font-mono text-purple-200 flex items-center gap-2">
            <span className="animate-spin">⟳</span>
            <span>記憶写真を保存中...</span>
          </div>
        </div>
      )}

      {/* スペーサー（タップ透過） */}
      <div className="flex-1" />

      {/* 字幕エリア（タップ透過） */}
      <div className="mx-4 mb-3">
        <div className="bg-black/70 backdrop-blur-md rounded-2xl border border-purple-500/20 px-4 py-3 shadow-xl">
          <p className="text-white text-sm leading-relaxed whitespace-pre-line text-center font-medium min-h-[1.5rem]">
            {subtitle || "ルキルキが現れるのを待っています..."}
          </p>
        </div>
      </div>

      {/* マーカーロスト中の顔アイコン（右下・タップ透過） */}
      {isTargetLost && <RukiFaceIcon aiStatus={aiStatus} />}

      {/* 入力エリア: ここだけタップを受け取る */}
      <div className="px-4 pb-6" style={{ pointerEvents: "auto" }}>
        {/* ボタン行 */}
        <div className="flex justify-between items-center mb-2 px-1">
          <button
            onClick={onToggleListen}
            className={`w-10 h-10 rounded-full border flex items-center justify-center text-lg transition-all ${
              isListening ? "bg-red-500/30 border-red-400/60 animate-pulse" : "bg-black/50 border-white/20 backdrop-blur-sm"
            }`}
            aria-label="音声入力"
          >🎙️</button>
          <button
            onClick={onCaptureSave}
            className="w-10 h-10 rounded-full border border-white/20 bg-black/50 backdrop-blur-sm flex items-center justify-center text-lg"
            aria-label="記憶写真を撮影"
          >📷</button>
          <button
            onClick={onOpenLog}
            className="w-10 h-10 rounded-full border border-white/20 bg-black/50 backdrop-blur-sm flex items-center justify-center text-lg"
            aria-label="ミッションログ"
          >📋</button>
        </div>

        {/* テキスト入力フォーム */}
        <form onSubmit={onSendMessage} className="flex gap-2 items-center">
          <input
            ref={inputRef}
            name="message"
            type="text"
            placeholder="ルキルキに話しかける..."
            autoComplete="off"
            // text-base(16px)を指定。iOS Safariは16px未満のinputにフォーカスすると
            // 自動的にページ全体をズームインする仕様があり、AR画面が毎回崩れる原因になっていた。
            className="flex-1 bg-black/60 backdrop-blur-md border border-purple-500/30 rounded-2xl px-4 py-3 text-base text-white placeholder-gray-500 focus:outline-none focus:border-purple-400/60 transition-colors"
          />
          <button
            type="submit"
            className="w-12 h-12 rounded-2xl bg-purple-600/80 border border-purple-400/50 flex items-center justify-center text-white text-lg hover:bg-purple-500/80 transition-colors active:scale-95"
            aria-label="送信"
          >➤</button>
        </form>
      </div>
    </div>
  );
}
