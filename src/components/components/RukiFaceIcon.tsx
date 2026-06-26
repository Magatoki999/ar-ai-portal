// components/RukiFaceIcon.tsx
"use client";

import { useEffect, useState } from "react";
import type { AIStatus, FacialEmotion } from "../lib/types";

interface RukiFaceIconProps {
  aiStatus: AIStatus;
  // セリフの意味合いから判定された感情。evaluator_node が品質評価と同時に分類する。
  // aiStatus が "talking" のときだけ参照される（thinking中は常にthinking表示、
  // idle中は常にidle表示で、facialEmotionは無視される）。
  facialEmotion?: FacialEmotion;
}

// 画像はすべて public/images/ 配下に配置されている。
const IDLE_FIRST_FRAME = "/images/idle_01.png";

// 2枚目（および、talking中に感情がある場合は1枚目も含めて）のファイル名キー。
// thinking中は常に "thinking"、idle中は常に "idle"。
// talking中だけ facialEmotion（fun/sad/worry/angry/neutral）で変わる。
// neutral は専用画像が無いため talking_02.png を使う。
function resolveSecondFrameKey(
  aiStatus: AIStatus,
  facialEmotion: FacialEmotion | undefined
): string {
  if (aiStatus === "thinking") return "thinking";
  if (aiStatus === "idle") return "idle";
  // aiStatus === "talking"
  if (facialEmotion && facialEmotion !== "neutral") return facialEmotion;
  return "talking";
}

export function RukiFaceIcon({ aiStatus, facialEmotion }: RukiFaceIconProps) {
  const [frameIndex, setFrameIndex] = useState<0 | 1>(0);

  // 1秒ごとに1枚目⇄2枚目を切り替えるインターバル。
  // ただし talking中に具体的な感情（fun/sad/worry/angry）がある場合は、
  // 1枚目も2枚目と同じ画像にして実質ストップモーションにする
  // （以前は1秒ごとにidle_01.pngへ戻ってしまい、感情がついたり消えたりするように
  //   見える問題があったため、会話中は感情をはっきり維持する仕様に変更）。
  useEffect(() => {
    const interval = setInterval(() => {
      setFrameIndex((prev) => (prev === 0 ? 1 : 0));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const secondFrameKey = resolveSecondFrameKey(aiStatus, facialEmotion);
  const secondFrame = `/images/${secondFrameKey}_02.png`;

  // talking中に具体的な感情がある場合だけ、1枚目も2枚目と同じ画像に固定する。
  // それ以外（idle/thinking/talkingでneutral）は、従来通り
  // idle_01.png（1枚目）⇄ 各状態の02（2枚目）のアニメーションを維持する。
  const hasSpecificEmotion =
    aiStatus === "talking" && !!facialEmotion && facialEmotion !== "neutral";
  const firstFrame = hasSpecificEmotion ? secondFrame : IDLE_FIRST_FRAME;

  const src = frameIndex === 0 ? firstFrame : secondFrame;

  return (
    <div
      className="fixed top-20 right-4 rounded-full overflow-hidden border border-purple-400/40 bg-black/40 backdrop-blur-sm shadow-lg"
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
