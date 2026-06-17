// hooks/useVoice.ts
// ─────────────────────────────────────────────────────────────────────────────
// 音声入出力をすべて管理するフック。
// 責務:
//   - Web Speech API（音声認識）の初期化・トグル
//   - AudioContext / AnalyserNode の初期化（initAudioPipeline）
//   - 口パク用モーフターゲット更新（requestAnimationFrame ループではなく
//     AnimationLoop から呼ばれる updateMouthMorph を提供する）
//   - base64 音声データを受け取って再生する playAudio()
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useEffect, useRef, useState } from "react";
import type { MorphTargetRef, AIStatus } from "../lib/types";
import { base64ToAudioUrl, resolveAudioMime } from "../lib/audio";

interface UseVoiceOptions {
  inputRef:         React.MutableRefObject<HTMLInputElement | null>;
  timersRef:        React.MutableRefObject<NodeJS.Timeout[]>;
  onAiStatusChange: (status: AIStatus) => void;
  onTranscript?:    (text: string) => void; // 音声認識結果を外部に渡すコールバック
}

export function useVoice({
  inputRef,
  timersRef,
  onAiStatusChange,
  onTranscript,
}: UseVoiceOptions) {
  const [isListening, setIsListening] = useState(false);

  const audioInstanceRef  = useRef<HTMLAudioElement | null>(null);
  const audioContextRef   = useRef<AudioContext | null>(null);
  const analyserRef       = useRef<AnalyserNode | null>(null);
  const freqDataRef       = useRef<Uint8Array | null>(null);
  const mouthTargetsRef   = useRef<MorphTargetRef[]>([]);
  const blinkTargetsRef   = useRef<MorphTargetRef[]>([]);
  const recognitionRef    = useRef<any>(null);

  // ── オーディオインスタンス初期化 ──
  useEffect(() => {
    audioInstanceRef.current = new Audio();
    return () => {
      audioInstanceRef.current?.pause();
      audioInstanceRef.current = null;
    };
  }, []);

  // ── Web Speech API 初期化 ──
  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous      = false;
    recognition.lang            = "ja-JP";
    recognition.interimResults  = false;

    recognition.onstart = () => {
      setIsListening(true);
    };
    recognition.onend = () => setIsListening(false);

    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript;
      if (onTranscript) {
        onTranscript(transcript);
      } else if (inputRef.current) {
        inputRef.current.value = transcript;
        // フォームの submit をトリガー
        const form = inputRef.current.form;
        if (form) form.requestSubmit();
      }
    };

    recognitionRef.current = recognition;
  }, []);

  // ── AudioContext パイプライン初期化 ──
  const initAudioPipeline = (audioInstance: HTMLAudioElement) => {
    if (!audioContextRef.current) {
      const AudioContextClass =
        window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx  = new AudioContextClass();
      const analyser  = audioCtx.createAnalyser();
      analyser.fftSize = 32;

      const source = audioCtx.createMediaElementSource(audioInstance);
      source.connect(analyser);
      analyser.connect(audioCtx.destination);

      audioContextRef.current = audioCtx;
      analyserRef.current     = analyser;
      freqDataRef.current     = new Uint8Array(analyser.frequencyBinCount);
    }
    if (audioContextRef.current.state === "suspended") {
      audioContextRef.current.resume();
    }
  };

  // ── 音声再生 ──
  const playAudio = async (
    base64: string,
    mimeType?: string,
    onEnded?: () => void
  ): Promise<void> => {
    if (!audioInstanceRef.current) return;

    const mime     = resolveAudioMime(mimeType);
    const audioUrl = base64ToAudioUrl(base64, mime);

    audioInstanceRef.current.onended = () => {
      URL.revokeObjectURL(audioUrl);
      onEnded?.();
    };

    initAudioPipeline(audioInstanceRef.current);
    audioInstanceRef.current.src = audioUrl;

    try {
      await audioInstanceRef.current.play();
    } catch (err) {
      console.log("[Voice] 音声再生失敗:", err);
      URL.revokeObjectURL(audioUrl);
      throw err;
    }
  };

  // ── 再生停止 ──
  const stopAudio = () => {
    if (audioInstanceRef.current) {
      audioInstanceRef.current.pause();
      audioInstanceRef.current.src = "";
    }
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
  };

  // ── 音声認識トグル ──
  const toggleListening = () => {
    if (!recognitionRef.current) return;
    if (isListening) {
      recognitionRef.current.stop();
    } else {
      stopAudio();
      audioContextRef.current?.resume();
      recognitionRef.current.start();
    }
  };

  // ── AnimationLoop から毎フレーム呼ばれる口パク更新 ──
  const updateMouthMorph = () => {
    const audio    = audioInstanceRef.current;
    const analyser = analyserRef.current;
    const freqData = freqDataRef.current;
    const targets  = mouthTargetsRef.current;

    if (!audio || !analyser || !freqData || targets.length === 0) return;

    if (!audio.paused) {
      analyser.getByteFrequencyData(freqData);
      let total = 0;
      for (let i = 0; i < freqData.length; i++) total += freqData[i];
      const weight = Math.min(((total / freqData.length) / 110) * 1.5, 1.0);
      const final  = weight > 0.05 ? weight : 0;
      targets.forEach((t) => t.idxs.forEach((idx) => (t.mesh.morphTargetInfluences[idx] = final)));
    } else {
      targets.forEach((t) => t.idxs.forEach((idx) => (t.mesh.morphTargetInfluences[idx] = 0)));
    }
  };

  return {
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
  };
}
