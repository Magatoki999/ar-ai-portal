// hooks/useWebSocket.ts
// ─────────────────────────────────────────────────────────────────────────────
// バックエンドとの WebSocket 常時接続を管理するフック。
// 責務:
//   - 接続・5秒後自動再接続
//   - proactive_speech メッセージの受信と音声再生
//   - target_found / target_lost / request_proactive の送信
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useEffect, useRef } from "react";
import type { AIStatus } from "../lib/types";
import { base64ToAudioUrl, resolveAudioMime } from "../lib/audio";

interface UseWebSocketOptions {
  audioInstanceRef:  React.MutableRefObject<HTMLAudioElement | null>;
  audioContextRef:   React.MutableRefObject<AudioContext | null>;
  timersRef:         React.MutableRefObject<NodeJS.Timeout[]>;
  initAudioPipeline: (audio: HTMLAudioElement) => void;
  onProactiveSpeech: (text: string, effect: string) => void;
  onAiStatusChange:  (status: AIStatus) => void;
}

export function useWebSocket({
  audioInstanceRef,
  audioContextRef,
  timersRef,
  initAudioPipeline,
  onProactiveSpeech,
  onAiStatusChange,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!baseUrl) return;

    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/avatar";
    let socket: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connect = () => {
      console.log(`📡 [空間同期リンク] 接続開始: ${wsUrl}`);
      socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onopen = () => {
        console.log("✨ [空間同期リンク] ルキルキとの常時接続（脳内リンク）が成功しました！");
      };

      socket.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === "proactive_speech") {
            console.log("🗣️ [ルキルキ自発的発話] 受信:", data.reply);

            // 再生中の音声を止める
            if (audioInstanceRef.current) {
              audioInstanceRef.current.pause();
              audioInstanceRef.current.src = "";
            }
            timersRef.current.forEach(clearTimeout);
            timersRef.current = [];

            const effect = data.spatial_effect ?? "cyber";
            onProactiveSpeech(data.reply, effect);

            if (data.audio_data && audioInstanceRef.current) {
              const mime     = resolveAudioMime(data.audio_mime);
              const audioUrl = base64ToAudioUrl(data.audio_data, mime);
              const audio    = audioInstanceRef.current;
              audio.onended = () => {
                onAiStatusChange("idle");
                URL.revokeObjectURL(audioUrl);
              };
              audio.onerror = () => {
                onAiStatusChange("idle");
                URL.revokeObjectURL(audioUrl);
              };
              try {
                initAudioPipeline(audio);
                audio.src = audioUrl;
                onAiStatusChange("talking");
                // 非ブロッキング：play() を await しない
                audio.play().catch(() => {
                  onAiStatusChange("idle");
                  URL.revokeObjectURL(audioUrl);
                });
              } catch {
                onAiStatusChange("idle");
                URL.revokeObjectURL(audioUrl);
              }
            } else {
              onAiStatusChange("talking");
              setTimeout(() => onAiStatusChange("idle"), 5000);
            }
          }
        } catch (err) {
          console.log("[WS] メッセージパース失敗:", err);
        }
      };

      socket.onclose = () => {
        console.log("🍂 [空間同期リンク] 切断。5秒後に再接続します。");
        wsRef.current = null;
        reconnectTimeout = setTimeout(() => {
          if (!wsRef.current) connect();
        }, 5000);
      };

      socket.onerror = (err) => {
        console.log("⚠️ [WS] エラー:", err);
      };
    };

    connect();

    return () => {
      socket?.close();
      clearTimeout(reconnectTimeout);
    };
  }, []);

  // ── 送信ヘルパー ──
  const send = (payload: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  };

  const notifyTargetFound  = () => send({ type: "target_found" });
  const notifyTargetLost   = () => send({ type: "target_lost" });
  const requestProactive   = () => send({ type: "request_proactive" });
  // 生成中の応答への割り込み。バックエンドの chat_endpoint が実行中の
  // LangGraph呼び出し（state.active_chat_task）をcancel()する合図。
  const sendInterrupt      = () => send({ type: "interrupt" });
  // 現在地の定期送信。会話していない間もspot_proximity_jobが「最後に分かった
  // 現在地」を参照できるよう、MindARViewer.tsx側から数分おきに呼ばれる想定。
  const sendLocationUpdate = (lat: number, lng: number) => send({ type: "location_update", lat, lng });

  return { wsRef, notifyTargetFound, notifyTargetLost, requestProactive, sendInterrupt, sendLocationUpdate };
}
