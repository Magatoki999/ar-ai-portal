// components/SnapViewer.tsx
// ─────────────────────────────────────────────────────────────────────────────
// スナップ写真・思い出写真のオーバーレイ表示コンポーネント。
// ─────────────────────────────────────────────────────────────────────────────
"use client";

interface SnapViewerProps {
  imageUrl: string;
  onClose:  () => void;
}

export function SnapViewer({ imageUrl, onClose }: SnapViewerProps) {
  return (
    <div
      className="fixed inset-0 bg-black/90 backdrop-blur-md flex flex-col items-center justify-center p-6 pointer-events-auto"
      style={{ zIndex: 200 }} // ⚠️ RukiHUD が z-index:100 を使うため、それより確実に高くする
    >
      {/* タイトル */}
      <div className="text-purple-400 font-mono text-xs tracking-widest mb-4">
        ::: MEMORY_SNAPSHOT :::
      </div>

      {/* 写真 */}
      <div className="relative max-w-sm w-full rounded-2xl overflow-hidden border border-purple-500/40 shadow-2xl shadow-purple-900/40">
        <img
          src={imageUrl}
          alt="思い出写真"
          className="w-full h-auto object-cover"
        />
        {/* グラデーションオーバーレイ */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/30 to-transparent pointer-events-none" />
      </div>

      {/* アクション行 */}
      <div className="flex gap-3 mt-6">
        <a
          href={imageUrl}
          download="rukiruki_memory.jpg"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs bg-purple-900/60 border border-purple-500/40 text-purple-200 px-5 py-2 rounded-xl hover:bg-purple-800/60 transition-colors font-mono"
        >
          ▼ SAVE
        </a>
        <button
          onClick={onClose}
          style={{ pointerEvents: "auto", cursor: "pointer" }}
          className="text-xs bg-gray-900/60 border border-gray-600/40 text-gray-300 px-5 py-2 rounded-xl hover:bg-gray-800/60 transition-colors font-mono"
        >
          CLOSE [X]
        </button>
      </div>
    </div>
  );
}
