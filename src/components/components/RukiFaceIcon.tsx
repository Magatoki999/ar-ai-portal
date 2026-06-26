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

// 1枚目は常に idle_01.png で固定。2枚目だけが状態/感情に応じて切り替わる。
// 画像はすべて public/images/ 配下に配置されている。
const FIRST_FRAME = "/images/idle_01.png";

// 2枚目のファイル名（拡張子・パスを除いた部分）。
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

  // 一時デバッグ用ログ：実際に渡されている props を確認する
  useEffect(() => {
    console.log("[RukiFaceIcon] props更新", { aiStatus, facialEmotion });
  }, [aiStatus, facialEmotion]);

  // 1秒ごとに 1枚目(idle_01固定) ⇄ 2枚目(可変) を切り替える。
  // aiStatus / facialEmotion が変わってもインターバル自体は張り直さず、
  // 参照する画像パスだけを切り替える。
  useEffect(() => {
    const interval = setInterval(() => {
      setFrameIndex((prev) => (prev === 0 ? 1 : 0));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const secondFrameKey = resolveSecondFrameKey(aiStatus, facialEmotion);
  const secondFrame = `/images/${secondFrameKey}_02.png`;
  const src = frameIndex === 0 ? FIRST_FRAME : secondFrame;

  // 一時デバッグ用ログ：実際に解決された画像パスを確認する
  useEffect(() => {
    console.log("[RukiFaceIcon] secondFrameKey=", secondFrameKey, "secondFrame=", secondFrame);
  }, [secondFrameKey, secondFrame]);

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
