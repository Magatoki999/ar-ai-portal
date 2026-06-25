// components/RukiFaceIcon.tsx
"use client";

import { useEffect, useState } from "react";
import type { AIStatus } from "../lib/types";

interface RukiFaceIconProps {
  aiStatus: AIStatus;
}

// aiStatus（idle/thinking/talking）ごとに2枚のフレームを1秒ごとに切り替える。
// マーカーロスト中、ルキルキの存在感が薄くならないよう右下に小さく表示するためのアイコン。
const FRAME_PATHS: Record<AIStatus, [string, string]> = {
  idle:     ["/idle_01.png",     "/idle_02.png"],
  thinking: ["/thinking_01.png", "/thinking_02.png"],
  talking:  ["/talking_01.png",  "/talking_02.png"],
};

export function RukiFaceIcon({ aiStatus }: RukiFaceIconProps) {
  const [frameIndex, setFrameIndex] = useState<0 | 1>(0);

  // 1秒ごとに 01 ⇄ 02 を切り替える。aiStatus が変わっても
  // インターバル自体は張り直さず、表示する画像パスだけを切り替える。
  useEffect(() => {
    const interval = setInterval(() => {
      setFrameIndex((prev) => (prev === 0 ? 1 : 0));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const frames = FRAME_PATHS[aiStatus] ?? FRAME_PATHS.idle;
  const src = frames[frameIndex];

  return (
    <div
      className="fixed bottom-24 right-4 rounded-full overflow-hidden border border-purple-400/40 bg-black/40 backdrop-blur-sm shadow-lg"
      style={{ width: 80, height: 80, zIndex: 110, pointerEvents: "none" }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt="ルキルキ"
        width={80}
        height={80}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </div>
  );
}
