"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

type AIStatus = "idle" | "thinking" | "talking";

// 💡 複数メッシュや左右分離キーに対応するための構造定義
interface MorphTargetRef {
  mesh: any;
  idxs: number[];
}

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");
  const [isListening, setIsListening] = useState<boolean>(false);
  const [currentDateTime, setCurrentDateTime] = useState<string>(""); // 💡 右上日時表示用のステート

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
  
  // 💡 自動スキャンされた口（リップシンク）の参照リスト
  const mouthTargetsRef = useRef<MorphTargetRef[]>([]);

  // 💡 自動スキャンされた瞬き（Blink）の参照リスト
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
        setSubtitle("（音声認識中...お話しください）");
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
          color: 0x8b5cf6, 
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
                    mat.emissive.setHex(0x080808); 
                  }
                }
                if (mat.roughness !== undefined) mat.roughness = 0.9;
                if (mat.metalness !== undefined) mat.metalness = 0.0;
              });
            }
          });

          blinkTargetsRef.current = localBlinkTargets;
          mouthTargetsRef.current = localMouthTargets;

          setSubtitle("ルキルキ召喚完了。");

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
          setSubtitle("ルキルキを現実世界に固定しました。話しかけてください。");
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
          setSubtitle("ターゲットを見失いました。");
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
        setSubtitle(`システム初期化エラー: ${errMsg}`);
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

  // 💡 Geolocation API を非同期処理（Promise化）するヘルパー
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
          resolve(null); // 位置情報が取れなくてもチャット自体はフォールバックして動かす
        },
        {
          enableHighAccuracy: true,
          timeout: 5000,
          maximumAge: 0,
        }
      );
    });
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
        audioInstance.play().catch(() => {});
        initAudioPipeline(audioInstance);
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
      audioInstance.play().catch(() => {});
      initAudioPipeline(audioInstance);
    }

    setSubtitle(`思考中... 「${text}」`);
    setAiStatus("thinking");

    // 💡 送信の直前に非同期で最新の位置情報をスキャン
    const location = await getGPSLocation();

    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            message: text, 
            wallet_address: address || null,
            latitude: location ? location.lat : null, // 💡 ペイロードに追加
            longitude: location ? location.lng : null  // 💡 ペイロードに追加
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
            for (let i = 0; i < len; i++) {
              bytes[i] = binaryString.charCodeAt(i);
            }
            const blob = new Blob([bytes], { type: "audio/mpeg" });
            const audioUrl = URL.createObjectURL(blob);

            audioInstance.onended = () => {
              setSubtitle("次の指示を待っています。");
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
              setSubtitle("次の指示を待っています。");
              setAiStatus("idle");
            }, 5000);
          }
        } else {
          setAiStatus("talking");
          setTimeout(() => {
            setSubtitle("次の指示を待っています。");
            setAiStatus("idle");
          }, 5000);
        }
        return;
      } catch (error) {
        console.error("通信エラー:", error);
        setSubtitle("バックエンドとの通信に失敗しました。");
        setAiStatus("idle");
        return;
      }
    }

    // Mock テスト環境用
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.value = "";
      }
      setSubtitle(`【本番フロントテスト】「${text}」を受信。`);
      setAiStatus("talking");
      setTimeout(() => {
        setSubtitle("次の指示を待っています.");
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
      `}} />

      <div
        ref={containerRef}
        className="mindar-full-container"
        style={{
          position: "fixed",
          top: 0, left: 0, width: "100vw", height: "100vh",
          overflow: "hidden", zIndex: 1, backgroundColor: "#000",
        }}
      />

      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-sans">
        
        {/* ─── 上部ヘッダーエリア ─── */}
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/50 backdrop-blur-md p-3 rounded-xl text-white border border-white/10">
          <span className="text-xs font-semibold flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${aiStatus === "thinking" ? "bg-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-green-400 animate-ping" : "bg-blue-400"}`} />
            STATUS: {aiStatus.toUpperCase()}
          </span>
          
          <div className="flex items-center gap-3">
            {/* 💡 右上日時表示エリア */}
            <span className="text-xs font-mono text-gray-300 bg-white/5 border border-white/10 px-2 py-1 rounded-md shadow-inner">
              {currentDateTime}
            </span>
            <div className="flex gap-2">
              <button onClick={() => setAiStatus("idle")} className="text-[10px] bg-gray-700 px-2 py-1 rounded">Idle</button>
              <button onClick={() => setAiStatus("thinking")} className="text-[10px] bg-yellow-600 px-2 py-1 rounded">Think</button>
              <button onClick={() => setAiStatus("talking")} className="text-[10px] bg-green-600 px-2 py-1 rounded">Talk</button>
            </div>
          </div>
        </div>

        {/* ─── 下部 UI エリア ─── */}
        <div className="w-full space-y-3 pointer-events-auto mb-4">
          <div className="bg-black/70 backdrop-blur-lg p-4 rounded-2xl text-white text-center min-h-[70px] flex items-center justify-center border border-white/10 shadow-xl">
            <p className="text-sm font-medium leading-relaxed transition-all duration-300 whitespace-pre-line">
              {subtitle}
            </p>
          </div>

          <form onSubmit={handleSendMessage} className="flex gap-2">
            <button
              type="button"
              onClick={toggleListening}
              className={`px-4 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-all pointer-events-auto ${
                isListening ? "bg-red-600 text-white animate-pulse" : "bg-gray-800 text-white border border-white/10 hover:bg-gray-700"
              }`}
            >
              {isListening ? "🛑" : "🎙️"}
            </button>

            <input 
              ref={inputRef}
              type="text" 
              name="message"
              disabled={aiStatus === "thinking"}
              placeholder={isListening ? "声を聴いています..." : "AI人格にメッセージを送信..."} 
              className="flex-1 bg-black/80 text-white border border-white/15 rounded-xl px-4 py-3.5 focus:outline-none focus:border-purple-500 text-sm placeholder-gray-500 backdrop-blur-md disabled:opacity-70 disabled:cursor-not-allowed"
            />
            <button 
              type="submit" 
              disabled={aiStatus === "thinking"}
              className="bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 text-white px-5 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-transform disabled:opacity-50"
            >
              送信
            </button>
          </form>
        </div>

      </div>
    </>
  );
}