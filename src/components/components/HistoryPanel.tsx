// components/HistoryPanel.tsx
// ─────────────────────────────────────────────────────────────────────────────
// 会話履歴パネル（ミッションログ）の表示コンポーネント。
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import type { HistoryItem } from "../lib/types";

interface HistoryPanelProps {
  history:   HistoryItem[];
  onClose:   () => void;
}

export function HistoryPanel({ history, onClose }: HistoryPanelProps) {
  return (
    <div className="fixed inset-0 bg-black/85 backdrop-blur-md z-50 flex flex-col p-6 font-mono text-white pointer-events-auto">
      {/* ヘッダー */}
      <div className="flex justify-between items-center border-b border-purple-500/30 pb-3 mb-4">
        <div className="flex flex-col">
          <span className="text-purple-400 font-bold tracking-widest text-sm">
            ::: RUKIRUKI_MISSION_LOG_RECORDER :::
          </span>
          <span className="text-[9px] text-gray-500">
            MAGATOKI LAB CORE MEMORY SYSTEM
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-xs bg-purple-950/60 border border-purple-500/40 text-purple-300 px-4 py-1.5 rounded-md hover:bg-purple-900/60 transition-colors font-bold"
        >
          CLOSE [X]
        </button>
      </div>

      {/* ログ一覧 */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-2 scrollbar-thin">
        {history.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-gray-500 text-xs py-12 gap-2">
            <span>─── 観測ログ履歴データが空です ───</span>
          </div>
        ) : (
          history.map((item, idx) => (
            <div
              key={idx}
              className={`p-3.5 rounded-xl border text-xs leading-relaxed shadow-md ${
                item.role === "user"
                  ? "bg-cyan-950/20 border-cyan-500/30 ml-12"
                  : "bg-purple-950/20 border-purple-500/30 mr-12"
              }`}
            >
              <div className="flex justify-between items-center mb-1.5 text-[10px] font-bold">
                <span
                  className={
                    item.role === "user" ? "text-cyan-400" : "text-purple-400"
                  }
                >
                  {item.role === "user" ? "▶ まがときさん" : "◁ ルキルキ SYSTEM"}
                </span>
                <span className="text-gray-500 font-normal">{item.timestamp}</span>
              </div>
              <p
                className={
                  item.role === "user" ? "text-cyan-100" : "text-purple-100"
                }
              >
                {item.text}
              </p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
