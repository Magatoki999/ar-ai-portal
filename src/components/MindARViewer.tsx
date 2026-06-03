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
  const [subtitle, setSubtitle] = useState<string>("SYSTEM_READY: ターゲットをシークしてください。");
  const [isListening, setIsListening] = useState<boolean>(false);
  const [currentDateTime, setCurrentDateTime] = useState<string>(""); 

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

  // 0. リアルタイム日時更新ロジック
  useEffect(() => {
    const updateDateTime = () => {
      const now = new Date();
      const yyyy = now.getFullYear();
      const mm = String(now.getMonth() + 1).padStart(2, "0");
      const dd = String(now.getDate()).padStart(2, "0");
      const hh = String(now.getHours()).padStart(2, "0");
      const min = String(now.getMinutes()).padStart(2, "0");
      const ss = String(now.getSeconds()).padStart(2, "0");
      setCurrentDateTime(`${yyyy}/${mm}/${dd} ${hh}:${min}:${ss}`);
    };
    updateDateTime();
    const timer = setInterval(updateDateTime, 1000);
    return () => clearInterval(timer);
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
        setSubtitle("SIGNAL_CAPTURED: 音声波形を同期中...お話しください。");
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
          color: 0x00ffcc, // 💡 ネオンシアンに変更してサイバー感を統一
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

              if (bIdxs.length > 0) {
                localBlinkTargets.push({ mesh: child, idxs: bIdxs });
              }
              if (mIdxs.length > 0) {
                localMouthTargets.push({ mesh: child, idxs: mIdxs });
              }
            }

            if (child.isMesh && child.material) {
              const materials = Array.isArray(child.material) ? child.material : [child.material];
              materials.forEach((mat) => {
                const isHair = child.name.toLowerCase().includes("hair") || (mat.name && mat.name.toLowerCase().includes("hair"));
                if (mat.emissive) {
                  if (isHair) {
                    mat.emissive.setHex(0x000000); 
                  } else {
                    mat.emissive.setHex(0x0a192f); // 💡 ほのかなサイバーブルーの環境発光
                  }
                }
                if (mat.roughness !== undefined) mat.roughness = 0.9;
                if (mat.metalness !== undefined) mat.metalness = 0.0;
              });
            }
          });

          blinkTargetsRef.current = localBlinkTargets;
          mouthTargetsRef.current = localMouthTargets;

          setSubtitle("RUKI_SYSTEM: ナビゲーター・ルキルキ 展開準備完了。");

          anchor.group.add(gltf.scene);

          if (gltf.animations.length > 0) {
            const mixer = new ThreeAnimationMixer(gltf.scene);
            mixerRef.current = mixer;
            
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
          setSubtitle("SYNC_SUCCESS: 現実世界に固定されました。回線オープン。");
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
          setSubtitle("SIGNAL_LOST: 空間シグナルを見失いました。再補足中...");
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
                  target.idxs.forEach((idx) => {
                    target.mesh.morphTargetInfluences[idx] = weight;
                  });
                });
              } else {
                blinkTargetsRef.current.forEach((target) => {
                  target.idxs.forEach((idx) => {
                    target.mesh.morphTargetInfluences[idx] = 0;
                  });
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
            for (let i = 0; i < freqDataRef.current.length; i++) {
              totalAmplitude += freqDataRef.current[i];
            }
            const averageVolume = totalAmplitude / freqDataRef.current.length;
            const morphWeight = Math.min((averageVolume / 110) * 1.5, 1.0);
            const finalWeight = morphWeight > 0.05 ? morphWeight : 0;

            mouthTargetsRef.current.forEach((target) => {
              target.idxs.forEach((idx) => {
                target.mesh.morphTargetInfluences[idx] = finalWeight;
              });
            });
          } else if (mouthTargetsRef.current.length > 0) {
            mouthTargetsRef.current.forEach((target) => {
              target.idxs.forEach((idx) => {
                target.mesh.morphTargetInfluences[idx] = 0;
              });
            });
          }

          renderer.render(scene, camera);
        });

      } catch (initError: any) {
        console.error("MindAR起動失敗:", initError);
        const errMsg = initError?.message || String(initError);
        setSubtitle(`CRITICAL_ERROR: ${errMsg}`);
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

  const getGPSLocation = (): Promise<{ lat: number; lng: number } | null> => {
    return new Promise((resolve) => {
      if (!navigator.geolocation) {
        console.warn("Geolocationはサポートされていません。");
        resolve(null);
        return;
      }
      
      navigator.geolocation.getCurrentPosition(
        (position) => {
          resolve({
            lat: position.coords.latitude,
            lng: position.coords.longitude,
          });
        },
        (error) => {
          console.error("GPS取得に失敗:", error);
          resolve(null); 
        },
        {
          enableHighAccuracy: true,
          timeout: 5000,
          maximumAge: 0,
        }
      );
    });
  };

  const captureARCameraFrame = (): string | null => {
    const video = containerRef.current?.querySelector("video");
    if (!video || video.videoWidth === 0) return null;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  };

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
        audioInstance.pause();
        audioInstance.src = ""; 
      }
      if (audioContextRef.current) {
        audioContextRef.current.resume();
      }
      recognitionRef.current.start();
    }
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
    }

    // 💡 👁️ 【新規追加】高レベルサイバー検索シミュレーター（段階的タイマー起動）
    const subtitleTimers: NodeJS.Timeout[] = [];
    
    setSubtitle(`🧠 QUANTUM COGNITION INITIALIZED...\n解析エンジン思考開始 ➔ "${text}"`);
    setAiStatus("thinking");

    subtitleTimers.push(setTimeout(() => {
      setSubtitle(`🌐 TAVILY INTELLIGENCE SEARCHING...\nインターネット・オープンデータ空間を多次元走査中...`);
    }, 2000));

    subtitleTimers.push(setTimeout(() => {
      setSubtitle(`🔮 TIME-MATRIX COMPILING...\n抽出データからルキルキが2026年時間軸の回答を再構成中...`);
    }, 5500));

    const location = await getGPSLocation();
    const imageBase64 = captureARCameraFrame();
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            message: text, 
            wallet_address: address || null,
            image_base64: imageBase64,       
            latitude: location ? location.lat : null, 
            longitude: location ? location.lng : null  
          }),
        });

        // 💡 レスポンスが到着したら即座にタイマーを全解除して最終回答表示へ
        subtitleTimers.forEach(timer => clearTimeout(timer));

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
            for (let i = 0; i < len; i++) {
              bytes[i] = binaryString.charCodeAt(i);
            }
            const blob = new Blob([bytes], { type: "audio/mpeg" });
            const audioUrl = URL.createObjectURL(blob);

            audioInstance.onended = () => {
              setSubtitle("STANDBY: 次の同期シーケンスを待機中...");
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
              setSubtitle("STANDBY: 次の同期シーケンスを待機中...");
              setAiStatus("idle");
            }, 5000);
          }
        } else {
          setAiStatus("talking");
          setTimeout(() => {
            setSubtitle("STANDBY: 次の同期シーケンスを待機中...");
            setAiStatus("idle");
          }, 5000);
        }
        return;
      } catch (error) {
        subtitleTimers.forEach(timer => clearTimeout(timer));
        console.error("通信エラー:", error);
        setSubtitle("CONNECTION_FAILED: 空間ネットワークの切断を検知しました。");
        setAiStatus("idle");
        return;
      }
    }

    // Mock テスト環境用
    setTimeout(() => {
      subtitleTimers.forEach(timer => clearTimeout(timer));
      if (inputRef.current) inputRef.current.value = "";
      setSubtitle(`【DEBUG_MODE】「${text}」データフレーム受信。`);
      setAiStatus("talking");
      setTimeout(() => {
        setSubtitle("STANDBY: 次の同期シーケンスを待機中...");
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
          top: 0 !important;
          left: 0 !important;
        }
        /* 💡 サイバー走査線エフェクト */
        .cyber-scanlines {
          position: fixed;
          top: 0; left: 0; width: 100vw; height: 100vh;
          background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.15) 50%);
          background-size: 100% 4px;
          z-index: 10;
          pointer-events: none;
        }
        /* 💡 画面端のブラウン管風Vignette */
        .cyber-vignette {
          position: fixed;
          inset: 0;
          box-shadow: inset 0 0 60px rgba(0, 255, 204, 0.1);
          z-index: 11;
          pointer-events: none;
        }
        /* 💡 マトリクス風プログレスインジケーター */
        @keyframes cyber-bar {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
        .animate-cyber-bar {
          animation: cyber-bar 1.5s infinite linear;
        }
      `}} />

      {/* ARバックグラウンド */}
      <div
        ref={containerRef}
        className="mindar-full-container"
        style={{
          position: "fixed",
          top: 0, left: 0, width: "100vw", height: "100vh",
          overflow: "hidden", zIndex: 1, backgroundColor: "#000",
        }}
      />

      {/* サイバー空間オーバーレイ */}
      <div className="cyber-scanlines" />
      <div className="cyber-vignette" />

      {/* メインUIレイヤー */}
      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-mono">
        
        {/* ─── 上部ヘッダーエリア ─── */}
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/75 backdrop-blur-md p-3 rounded-xl text-white border border-[#00ffcc]/20 shadow-[0_0_15px_rgba(0,255,204,0.1)]">
          <span className="text-xs font-semibold flex items-center gap-2 text-[#00ffcc]">
            <span className={`h-2.5 w-2.5 rounded-full ${aiStatus === "thinking" ? "bg-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-[#00ffcc] animate-ping" : "bg-blue-400"}`} />
            CORE_STATUS // {aiStatus.toUpperCase()}
          </span>
          
          <div className="flex items-center gap-3">
            <span className="text-[11px] font-mono text-[#00ffcc] bg-[#00ffcc]/5 border border-[#00ffcc]/20 px-2 py-1 rounded-md shadow-[inset_0_0_8px_rgba(0,255,204,0.1)]">
              {currentDateTime}
            </span>
          </div>
        </div>

        {/* ─── 下部 UI エリア ─── */}
        <div className="w-full space-y-3 pointer-events-auto mb-4">
          
          {/* サブタイトルターミナル */}
          <div className={`bg-black/80 backdrop-blur-lg p-4 rounded-xl text-white min-h-[85px] flex flex-col justify-center border transition-all duration-300 relative overflow-hidden ${
            aiStatus === "thinking" ? "border-yellow-400 shadow-[0_0_15px_rgba(234,179,8,0.2)]" : "border-[#00ffcc]/30 shadow-[0_0_15px_rgba(0,255,204,0.1)]"
          }`}>
            
            {/* 💡 検索思考中（thinking）限定のネオン進捗バー */}
            {aiStatus === "thinking" && (
              <div className="absolute top-0 left-0 w-full h-[2px] bg-black overflow-hidden">
                <div className="w-1/3 h-full bg-gradient-to-r from-transparent via-yellow-400 to-transparent animate-cyber-bar" />
              </div>
            )}

            <p className={`text-xs font-semibold uppercase tracking-wider mb-1 ${aiStatus === "thinking" ? "text-yellow-400" : "text-[#00ffcc]/70"}`}>
              {aiStatus === "thinking" ? "≫ NETWORK_SCANNING_SEQUENCE" : "≫ RUKI_NET_INTERFACE"}
            </p>
            <p className="text-sm font-medium leading-relaxed transition-all duration-300 whitespace-pre-line text-gray-100">
              {subtitle}
            </p>
          </div>

          {/* 入力コンソール */}
          <form onSubmit={handleSendMessage} className="flex gap-2">
            <button
              type="button"
              onClick={toggleListening}
              className={`px-4 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-all pointer-events-auto border ${
                isListening 
                  ? "bg-red-900/60 text-red-200 border-red-500 animate-pulse shadow-[0_0_15px_rgba(239,68,68,0.4)]" 
                  : "bg-black/60 text-[#00ffcc] border-[#00ffcc]/20 hover:bg-[#00ffcc]/10 shadow-[inset_0_0_10px_rgba(0,255,204,0.05)]"
              }`}
            >
              {isListening ? "⚡" : "LN"}
            </button>

            <input 
              ref={inputRef}
              type="text" 
              name="message"
              disabled={aiStatus === "thinking"}
              placeholder={isListening ? "🎧 SYSTEM LISTENING..." : "COMMAND > ルキルキへの送信指示を入力..."} 
              className="flex-1 bg-black/90 text-white border border-white/10 rounded-xl px-4 py-3.5 focus:outline-none focus:border-[#00ffcc] focus:shadow-[0_0_10px_rgba(0,255,204,0.2)] text-sm placeholder-gray-600 backdrop-blur-md disabled:opacity-50 disabled:cursor-not-allowed font-mono transition-all"
            />
            <button 
              type="submit" 
              disabled={aiStatus === "thinking"}
              className="bg-gradient-to-r from-[#111] to-[#222] text-[#00ffcc] border border-[#00ffcc]/40 hover:border-[#00ffcc] px-5 py-3.5 rounded-xl font-bold text-sm shadow-[0_0_10px_rgba(0,255,204,0.1)] active:scale-95 transition-all disabled:opacity-30 disabled:border-gray-700 disabled:text-gray-600"
            >
              EXEC
            </button>
          </form>
        </div>

      </div>
    </>
  );
}