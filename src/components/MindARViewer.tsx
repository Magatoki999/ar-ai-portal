"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

type AIStatus = "idle" | "thinking" | "talking";

interface MorphTargetRef {
  mesh: any;
  idxs: number[];
}

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [subtitle, setSubtitle] = useState<string>("[SYS_READY] CAMERA STREAM STANDBY...");
  const [isListening, setIsListening] = useState<boolean>(false);
  
  // ─── 【新設】SF時計用のステート ───
  const [timeString, setTimeString] = useState<string>("");
  const [dateString, setDateString] = useState<string>("");

  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { address } = useAccount();

  // Three.js Animation References
  const mixerRef = useRef<AnimationMixer | null>(null);
  const actionsRef = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);

  // Audio Pipeline References for Smart Lip-Sync
  const audioInstanceRef = useRef<HTMLAudioElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const freqDataRef = useRef<Uint8Array | null>(null);
  
  const mouthTargetsRef = useRef<MorphTargetRef[]>([]);
  const blinkTargetsRef = useRef<MorphTargetRef[]>([]);
  const avatarSceneRef = useRef<any>(null);

  // References for Magatoki Spawn Particles & Animation
  const particlesRef = useRef<any>(null);
  const particleVelocitiesRef = useRef<Float32Array | null>(null);
  const spawnProgressRef = useRef<number>(0);
  const isSpawningRef = useRef<boolean>(false);

  // ─── 【新設】SFリアルタイム時計の駆動ループ ───
  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      
      // 時間形式: 23:04:15
      const hrs = String(now.getHours()).padStart(2, '0');
      const mins = String(now.getMinutes()).padStart(2, '0');
      const secs = String(now.getSeconds()).padStart(2, '0');
      setTimeString(`${hrs}:${mins}:${secs}`);

      // 日付形式: 2026.06.02 [TUE]
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const date = String(now.getDate()).padStart(2, '0');
      const days = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
      const dayName = days[now.getDay()];
      setDateString(`${year}.${month}.${date} [${dayName}]`);
    };

    updateClock();
    const timerId = setInterval(updateClock, 1000);
    return () => clearInterval(timerId);
  }, []);

  // 1. Initialize Global Audio Instance on Mount
  useEffect(() => {
    audioInstanceRef.current = new Audio();
    return () => {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause();
        audioInstanceRef.current = null;
      }
    };
  }, []);

  // 2. Smoothly crossfade 3D model animations
  useEffect(() => {
    const fadeToAction = (status: AIStatus, duration: number = 0.5) => {
      const nextAction = actionsRef.current[status];
      const currentAction = activeActionRef.current;

      if (!nextAction || nextAction === currentAction) return;

      nextAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(duration).play();
      if (currentAction) currentAction.fadeOut(duration);
      activeActionRef.current = nextAction;
    };
    fadeToAction(aiStatus);
  }, [aiStatus]);

  // 3. Initialize Web Speech API
  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.lang = "ja-JP";
      recognition.interimResults = false;

      recognition.onstart = () => {
        setIsListening(true);
        setSubtitle(">>> [AUDIO_INPUT] VOICE CAPTURING...");
      };
      recognition.onend = () => setIsListening(false);
      recognition.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        if (inputRef.current) {
          inputRef.current.value = transcript;
          const form = inputRef.current.form;
          if (form) form.requestSubmit();
        }
      };
      recognitionRef.current = recognition;
    }
  }, []);

  const initAudioPipeline = (audioInstance: HTMLAudioElement) => {
    if (!audioContextRef.current) {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioContextClass();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 32;
      
      const source = audioCtx.createMediaElementSource(audioInstance);
      source.connect(analyser);
      analyser.connect(audioCtx.destination);
      
      audioContextRef.current = audioCtx;
      analyserRef.current = analyser;
      freqDataRef.current = new Uint8Array(analyser.frequencyBinCount);
    }
    if (audioContextRef.current.state === "suspended") {
      audioContextRef.current.resume();
    }
  };

  // 4. Initialize MindAR and Three.js environment
  useEffect(() => {
    let mindarThreeInstance: any = null;

    const start = async () => {
      try {
        const THREE = await import("three");
        const { MindARThree } = await import("mind-ar/dist/mindar-image-three.prod.js");
        const { GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js");
        const { DRACOLoader } = await import("three/examples/jsm/loaders/DRACOLoader.js");

        const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE;

        if (!containerRef.current) throw new Error("DOMコンテナが見つかりません。");

        const mindarThree = new MindARThree({
          container: containerRef.current,
          imageTargetSrc: "/targets.mind",
        });
        mindarThreeInstance = mindarThree;

        const { renderer, scene, camera } = mindarThree;

        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.0; 

        const ambientLight = new THREE.AmbientLight(0xffffff, 1.2); 
        scene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.6); 
        directionalLight.position.set(0, 2, 10); 
        scene.add(directionalLight);

        const anchor = mindarThree.addAnchor(0);

        // Setup Cyber Ink Particles
        const particleCount = 70;
        const particleGeometry = new THREE.BufferGeometry();
        const particlePositions = new Float32Array(particleCount * 3);
        const particleVelocities = new Float32Array(particleCount * 3);

        for (let i = 0; i < particleCount; i++) {
          particlePositions[i * 3] = 0;
          particlePositions[i * 3 + 1] = 0;
          particlePositions[i * 3 + 2] = 0;

          particleVelocities[i * 3] = (Math.random() - 0.5) * 0.6;
          particleVelocities[i * 3 + 1] = Math.random() * 0.8 + 0.2; 
          particleVelocities[i * 3 + 2] = (Math.random() - 0.5) * 0.6;
        }

        particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
        
        const particleMaterial = new THREE.PointsMaterial({
          color: 0x06b6d4, // シアンに変更してSF感を強化
          size: 0.035,
          transparent: true,
          opacity: 0,
          blending: THREE.AdditiveBlending
        });

        const spawnParticles = new THREE.Points(particleGeometry, particleMaterial);
        anchor.group.add(spawnParticles);
        particlesRef.current = spawnParticles;
        particleVelocitiesRef.current = particleVelocities;

        const dracoLoader = new DRACOLoader();
        dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");

        const loader = new GLTFLoader();
        loader.setDRACOLoader(dracoLoader);

        const localBlinkTargets: MorphTargetRef[] = [];
        const localMouthTargets: MorphTargetRef[] = [];

        loader.load("/avatar.glb?v=9", (gltf) => {
          gltf.scene.scale.set(0, 0, 0); 
          gltf.scene.rotation.x = Math.PI / 2;
          avatarSceneRef.current = gltf.scene;

          gltf.scene.traverse((child: any) => {
            if (child.isMesh && child.morphTargetDictionary) {
              const bIdxs: number[] = [];
              const mIdxs: number[] = [];

              Object.keys(child.morphTargetDictionary).forEach((key) => {
                const lowKey = key.toLowerCase();
                
                if (
                  lowKey === "blink" || 
                  lowKey === "eyeblink" ||
                  lowKey === "close" ||
                  lowKey.includes("eye_close") || 
                  lowKey.includes("eye-close") ||
                  lowKey.includes("blink_") ||
                  lowKey.includes("blinkleft") ||
                  lowKey.includes("blinkright")
                ) {
                  bIdxs.push(child.morphTargetDictionary[key]);
                }

                if (
                  lowKey === "aa" || 
                  lowKey === "a" || 
                  lowKey === "vowel_a" ||
                  lowKey === "oto_a" ||
                  lowKey.includes("mouth_a") || 
                  lowKey.includes("mth_a") ||
                  lowKey.includes("mouth_open_a")
                ) {
                  mIdxs.push(child.morphTargetDictionary[key]);
                }
              });

              if (bIdxs.length > 0) localBlinkTargets.push({ mesh: child, idxs: bIdxs });
              if (mIdxs.length > 0) localMouthTargets.push({ mesh: child, idxs: mIdxs });
            }

            if (child.isMesh && child.material) {
              const materials = Array.isArray(child.material) ? child.material : [child.material];
              materials.forEach((mat) => {
                const isHair = child.name.toLowerCase().includes("hair") || (mat.name && mat.name.toLowerCase().includes("hair"));
                if (mat.emissive) {
                  mat.emissive.setHex(isHair ? 0x000000 : 0x080808);
                }
                if (mat.roughness !== undefined) mat.roughness = 0.9;
                if (mat.metalness !== undefined) mat.metalness = 0.0;
              });
            }
          });

          blinkTargetsRef.current = localBlinkTargets;
          mouthTargetsRef.current = localMouthTargets;

          setSubtitle("[SYS_INFO] RUKIRUKI MODULE INITIALIZED.");
          anchor.group.add(gltf.scene);

          if (gltf.animations.length > 0) {
            const mixer = new ThreeAnimationMixer(gltf.scene);
            mixerRef.current = mixer;
            
            // ─── 【維持】ユーザー様の最新アニメーションインデックス ───
            actionsRef.current["idle"] = mixer.clipAction(gltf.animations[0]);
            actionsRef.current["talking"] = mixer.clipAction(gltf.animations[2] || gltf.animations[0]);
            actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[1] || gltf.animations[0]);

            activeActionRef.current = actionsRef.current["idle"];
            activeActionRef.current.play();
          }
        }, undefined, (error) => {
          console.error("モデル読み込み失敗:", error);
        });

        anchor.onTargetFound = () => {
          setSubtitle(">>> [LINK_ESTABLISHED] RUKIRUKI SYNCED.");
          spawnProgressRef.current = 0;
          isSpawningRef.current = true;

          if (particlesRef.current) {
            (particlesRef.current.material as any).opacity = 1.0;
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            for (let i = 0; i < particleCount; i++) {
              posArr[i * 3] = 0;
              posArr[i * 3 + 1] = -0.2; 
              posArr[i * 3 + 2] = 0;
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
          }
        };

        anchor.onTargetLost = () => {
          setSubtitle(">>> [LINK_LOST] SEARCHING TARGET MARKER...");
          isSpawningRef.current = false;
          if (avatarSceneRef.current) {
            avatarSceneRef.current.scale.set(0, 0, 0); 
          }
        };

        const clock = new Clock();
        let blinkTimer = 0;
        let isBlinking = false;
        let blinkDuration = 0.14; 
        let nextBlinkTime = 2.0 + Math.random() * 4.0; 

        await mindarThree.start();

        renderer.setAnimationLoop(() => {
          const delta = clock.getDelta();
          const elapsedTime = clock.getElapsedTime();
          
          if (mixerRef.current) mixerRef.current.update(delta);
          
          if (isSpawningRef.current && avatarSceneRef.current) {
            if (spawnProgressRef.current < 1.0) {
              spawnProgressRef.current += delta * 1.8; 
              const progress = Math.min(spawnProgressRef.current, 1.0);
              const easeOutCubic = 1 - Math.pow(1 - progress, 3);
              avatarSceneRef.current.scale.set(easeOutCubic, easeOutCubic, easeOutCubic);
            } else {
              isSpawningRef.current = false;
            }
          }

          if (avatarSceneRef.current && !isSpawningRef.current && spawnProgressRef.current >= 1.0) {
            avatarSceneRef.current.position.y = Math.sin(elapsedTime * 1.8) * 0.012;
          }

          if (blinkTargetsRef.current.length > 0) {
            blinkTimer += delta;
            if (!isBlinking && blinkTimer >= nextBlinkTime) {
              isBlinking = true;
              blinkTimer = 0;
            }
            if (isBlinking) {
              if (blinkTimer < blinkDuration) {
                const progress = blinkTimer / blinkDuration;
                const weight = Math.sin(progress * Math.PI); 
                blinkTargetsRef.current.forEach((target) => {
                  target.idxs.forEach((idx) => { target.mesh.morphTargetInfluences[idx] = weight; });
                });
              } else {
                blinkTargetsRef.current.forEach((target) => {
                  target.idxs.forEach((idx) => { target.mesh.morphTargetInfluences[idx] = 0; });
                });
                isBlinking = false;
                blinkTimer = 0;
                nextBlinkTime = 1.5 + Math.random() * 4.5; 
              }
            }
          }

          if (particlesRef.current && particleVelocitiesRef.current) {
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            const vels = particleVelocitiesRef.current;
            
            for (let i = 0; i < particleCount; i++) {
              posArr[i * 3] += vels[i * 3] * delta;
              posArr[i * 3 + 1] += vels[i * 3 + 1] * delta;
              posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
              vels[i * 3 + 1] -= delta * 0.2;
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
            
            if ((particlesRef.current.material as any).opacity > 0) {
              (particlesRef.current.material as any).opacity -= delta * 1.4;
            }
          }

          const audioInstance = audioInstanceRef.current;
          const isVoicePlaying = audioInstance && !audioInstance.paused;

          if (isVoicePlaying && analyserRef.current && freqDataRef.current && mouthTargetsRef.current.length > 0) {
            analyserRef.current.getByteFrequencyData(freqDataRef.current);
            let totalAmplitude = 0;
            for (let i = 0; i < freqDataRef.current.length; i++) { totalAmplitude += freqDataRef.current[i]; }
            const averageVolume = totalAmplitude / freqDataRef.current.length;
            const morphWeight = Math.min((averageVolume / 110) * 1.5, 1.0);
            const finalWeight = morphWeight > 0.05 ? morphWeight : 0;

            mouthTargetsRef.current.forEach((target) => {
              target.idxs.forEach((idx) => { target.mesh.morphTargetInfluences[idx] = finalWeight; });
            });
          } else if (mouthTargetsRef.current.length > 0) {
            mouthTargetsRef.current.forEach((target) => {
              target.idxs.forEach((idx) => { target.mesh.morphTargetInfluences[idx] = 0; });
            });
          }

          renderer.render(scene, camera);
        });

      } catch (initError: any) {
        console.error("MindAR起動失敗:", initError);
        const errMsg = initError?.message || String(initError);
        setSubtitle(`[CRITICAL_ERR] INITIALIZATION FAILED: ${errMsg}`);
        alert(`🚨 ARカメラ起動エラー:\n${errMsg}`);
      }
    };

    start();

    return () => {
      if (mindarThreeInstance) {
        try { mindarThreeInstance.stop(); } catch(e){}
      }
    };
  }, []);

  const toggleListening = () => {
    if (!recognitionRef.current) {
      alert("お使いのブラウザは音声認識に対応していません。ChromeかSafariでお試しください。");
      return;
    }

    if (isListening) {
      recognitionRef.current.stop();
    } else {
      const audioInstance = audioInstanceRef.current;
      if (audioInstance) {
        audioInstance.play().catch(() => {});
        initAudioPipeline(audioInstance);
      }
      recognitionRef.current.start();
    }
  };

  // ─── 【追加】Vision用のカメラ映像JPEGキャプチャロジック ───
  const captureCameraFrame = (): string | null => {
    const video = containerRef.current?.querySelector("video") || document.querySelector("video");
    if (!video) return null;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth > 0 ? video.videoWidth / 2 : 640;
    canvas.height = video.videoHeight > 0 ? video.videoHeight / 2 : 480;

    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  };

  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text = formData.get("message") as string;
    if (!text.trim()) return;

    const audioInstance = audioInstanceRef.current;
    if (audioInstance) {
      audioInstance.pause();
      audioInstance.src = ""; 
      audioInstance.play().catch(() => {});
      initAudioPipeline(audioInstance);
    }

    setSubtitle(`>>> [PROCESSING] COMPUTE QUANTUM LOGIC...`);
    setAiStatus("thinking");

    // カメラの現在のフレームを取得
    const imageBase64 = captureCameraFrame();
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            message: text, 
            wallet_address: address || null,
            image_base64: imageBase64  // ─── 【追加】画像をバックエンドへ送信 ───
          }),
        });

        if (!response.ok) throw new Error("APIへの接続に失敗しました");

        const data = await response.json();
        
        if (inputRef.current) {
          inputRef.current.value = "";
        }

        setSubtitle(data.reply);

        if (data.audio_data && audioInstance) {
          try {
            const binaryString = window.atob(data.audio_data);
            const len = binaryString.length;
            const bytes = new Uint8Array(len);
            for (let i = 0; i < len; i++) { bytes[i] = binaryString.charCodeAt(i); }
            const blob = new Blob([bytes], { type: "audio/mpeg" });
            const audioUrl = URL.createObjectURL(blob);

            audioInstance.onended = () => {
              setSubtitle(">>> [STANDBY] AWAITING NEXT TELEMETRY INPUT...");
              setAiStatus("idle");
              URL.revokeObjectURL(audioUrl); 
            };

            audioInstance.src = audioUrl;
            setAiStatus("talking");
            await audioInstance.play();

          } catch (audioError) {
            console.error("音声再生エラー。フォールバック処理を行います:", audioError);
            setAiStatus("talking");
            setTimeout(() => {
              setSubtitle(">>> [STANDBY] PIPELINE FALLBACK COMPLETED.");
              setAiStatus("idle");
            }, 5000);
          }
        } else {
          setAiStatus("talking");
          setTimeout(() => {
            setSubtitle(">>> [STANDBY] AWAITING NEXT TELEMETRY INPUT...");
            setAiStatus("idle");
          }, 5000);
        }
        return;
      } catch (error) {
        console.error("通信エラー:", error);
        setSubtitle("[ERR] QUANTUM LINK TIMEOUT. SIGNAL BLOCKED.");
        setAiStatus("idle");
        return;
      }
    }

    // Mock テスト環境用
    setTimeout(() => {
      if (inputRef.current) { inputRef.current.value = ""; }
      setSubtitle(`[MOCK_SYS] RECEIVE: "${text}"`);
      setAiStatus("talking");
      setTimeout(() => {
        setSubtitle(">>> [STANDBY] AWAITING NEXT TELEMETRY INPUT...");
        setAiStatus("idle");
      }, 5000);
    }, 2000);
  };

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: `
        .mindar-full-container video,
        .mindar-full-container canvas {
          width: 100vw !important;
          height: 100vh !important;
          object-fit: cover !important;
          position: fixed !important;
          top: 0 !important; left: 0 !important;
        }
      `}} />

      <div
        ref={containerRef}
        className="mindar-full-container"
        style={{
          position: "fixed", top: 0, left: 0, width: "100vw", height: "100vh",
          overflow: "hidden", zIndex: 1, backgroundColor: "#000",
        }}
      />

      {/* ─── SF調に磨き上げたオーバーレイUI ─── */}
      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-mono select-none">
        
        {/* 上部ヘッダー：サイバーボーダーとスキャン状態 */}
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/60 backdrop-blur-md px-4 py-3 rounded-xl text-white border border-cyan-500/30 shadow-[0_0_15px_rgba(6,182,212,0.15)]">
          <span className="text-xs font-bold flex items-center gap-2.5 tracking-widest text-cyan-400">
            <span className={`h-2 w-2 rounded-full shadow-[0_0_8px_currentColor] ${aiStatus === "thinking" ? "bg-yellow-400 text-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-cyan-400 text-cyan-400 animate-ping" : "bg-purple-500 text-purple-500"}`} />
            MAGATOKI_SYS: {aiStatus.toUpperCase()}
          </span>
          <div className="text-[9px] text-gray-400 flex gap-3 tracking-wider">
            <span>LINK: <span className="text-green-400">SECURE</span></span>
            <span>FPS: 60</span>
          </div>
        </div>

        {/* 右上隅：SFリアルタイムクロック & システムメタデータ */}
        <div className="absolute top-20 right-4 text-right font-mono text-[10px] text-cyan-400 tracking-widest space-y-0.5 bg-black/50 px-3 py-2 rounded-lg border border-cyan-500/20 backdrop-blur-sm pointer-events-auto shadow-md">
          <div className="text-gray-500 text-[8px] border-b border-cyan-500/20 pb-0.5 mb-1 text-center font-bold">GATEWAY TELEMETRY</div>
          <div>NODE_STABLE: 99.4%</div>
          <div className="text-gray-300">{dateString}</div>
          <div className="text-xs font-bold text-cyan-300 text-shadow-cyan animate-pulse tracking-normal">{timeString} <span className="text-[9px] font-normal text-cyan-500">JST</span></div>
        </div>

        {/* 下部：字幕コンテナと入力ブロック */}
        <div className="w-full space-y-3.5 pointer-events-auto mb-4">
          
          {/* 字幕エリア：光るサイバーフレーム */}
          <div className="relative bg-black/75 backdrop-blur-xl px-5 py-4.5 rounded-2xl text-white border border-purple-500/30 shadow-[0_0_20px_rgba(139,92,246,0.15)] min-h-[80px] flex items-center">
            {/* 角のL字装飾 */}
            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-cyan-400 rounded-tl" />
            <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-cyan-400 rounded-tr" />
            <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-cyan-400 rounded-bl" />
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-cyan-400 rounded-br" />
            
            <p className="text-xs font-medium leading-relaxed tracking-wider text-gray-100 whitespace-pre-line w-full">
              {subtitle}
            </p>
          </div>

          {/* 入力フォーム */}
          <form onSubmit={handleSendMessage} className="flex gap-2">
            <button
              type="button"
              onClick={toggleListening}
              className={`px-4.5 py-3.5 rounded-xl font-bold text-sm shadow-lg active:scale-95 transition-all border ${
                isListening 
                  ? "bg-red-600/20 text-red-400 border-red-500 shadow-[0_0_10px_rgba(239,68,68,0.4)] animate-pulse" 
                  : "bg-black/70 text-cyan-400 border-cyan-500/40 hover:bg-cyan-950/40"
              }`}
            >
              {isListening ? "SYNC" : "🎙️"}
            </button>

            <input 
              ref={inputRef}
              type="text" 
              name="message"
              disabled={aiStatus === "thinking"}
              placeholder={isListening ? "<< LISTENING AUDIO STREAM >>" : "INPUT COMMAND TO RUKIRUKI..."} 
              className="flex-1 bg-black/80 text-cyan-200 border border-purple-500/30 rounded-xl px-4 py-3.5 focus:outline-none focus:border-cyan-400 text-xs tracking-wider placeholder-gray-600 backdrop-blur-md disabled:opacity-50 disabled:cursor-not-allowed shadow-inner"
            />
            
            <button 
              type="submit" 
              disabled={aiStatus === "thinking"}
              className="bg-gradient-to-r from-purple-700 to-cyan-700 hover:from-purple-600 hover:to-cyan-600 text-white font-bold px-6 py-3.5 rounded-xl text-xs tracking-widest border border-cyan-400/20 shadow-md active:scale-95 transition-transform disabled:opacity-40"
            >
              EXEC
            </button>
          </form>
        </div>

      </div>
    </>
  );
}