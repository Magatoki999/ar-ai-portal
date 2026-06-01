"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

type AIStatus = "idle" | "thinking" | "talking";

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");
  const [isListening, setIsListening] = useState<boolean>(false);
  
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
  
  // References for Mouth Lip-Sync
  const faceMeshRef = useRef<any>(null);
  const mouthTargetIdxRef = useRef<number | null>(null);

  // 💡 [NEW] References for Blink & Natural Motion
  const blinkMeshRef = useRef<any>(null);
  const blinkTargetIdxRef = useRef<number | null>(null);
  const avatarSceneRef = useRef<any>(null);

  // 💡 [NEW] References for Magatoki Spawn Particles & Animation
  const particlesRef = useRef<any>(null);
  const particleVelocitiesRef = useRef<Float32Array | null>(null);
  const spawnProgressRef = useRef<number>(0);
  const isSpawningRef = useRef<boolean>(false);

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
      const THREE = await import("three");
      const { MindARThree } = await import("mind-ar/dist/mindar-image-three.prod.js");
      const { GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js");
      const { DRACOLoader } = await import("three/examples/jsm/loaders/DRACOLoader.js");

      const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE;

      const mindarThree = new MindARThree({
        container: containerRef.current!,
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

      // 💡 [NEW] Setup Cyber Ink Particles for Magatoki Spawn Effect
      const particleCount = 70;
      const particleGeometry = new THREE.BufferGeometry();
      const particlePositions = new Float32Array(particleCount * 3);
      const particleVelocities = new Float32Array(particleCount * 3);

      for (let i = 0; i < particleCount; i++) {
        // Start clustered at the center anchor point
        particlePositions[i * 3] = 0;
        particlePositions[i * 3 + 1] = 0;
        particlePositions[i * 3 + 2] = 0;

        // Spread outwards and upwards
        particleVelocities[i * 3] = (Math.random() - 0.5) * 0.6;
        particleVelocities[i * 3 + 1] = Math.random() * 0.8 + 0.2; // Upward bias
        particleVelocities[i * 3 + 2] = (Math.random() - 0.5) * 0.6;
      }

      particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
      
      const particleMaterial = new THREE.PointsMaterial({
        color: 0x8b5cf6, // Magatoki Cyber Violet
        size: 0.035,
        transparent: true,
        opacity: 0,
        blending: THREE.AdditiveBlending
      });

      const spawnParticles = new THREE.Points(particleGeometry, particleMaterial);
      anchor.group.add(spawnParticles);
      particlesRef.current = spawnParticles;
      particleVelocitiesRef.current = particleVelocities;

      // Setup DRACO decoder
      const dracoLoader = new DRACOLoader();
      dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");

      const loader = new GLTFLoader();
      loader.setDRACOLoader(dracoLoader);

      // Load avatar asset with cache buster v7
      loader.load("/avatar.glb?v=7", (gltf) => {
        gltf.scene.scale.set(0, 0, 0); // Start at 0 for spawn animation
        gltf.scene.rotation.x = Math.PI / 2;
        avatarSceneRef.current = gltf.scene;

        gltf.scene.traverse((child: any) => {
          // Locate Mouth Target for Lip-Sync
          if (child.isMesh && child.morphTargetDictionary) {
            const mouthCandidates = ["aa", "Fcl_Mth_A", "Mouth_A", "A", "Oto_A"];
            for (const key of mouthCandidates) {
              if (child.morphTargetDictionary[key] !== undefined) {
                faceMeshRef.current = child;
                mouthTargetIdxRef.current = child.morphTargetDictionary[key];
                break;
              }
            }

            // 💡 [NEW] Locate Eye Close Target for Random Automatic Blinking
            const blinkCandidates = ["blink", "Fcl_Eye_Close", "Eye_Close", "EYE_CLOSE"];
            for (const key of blinkCandidates) {
              if (child.morphTargetDictionary[key] !== undefined) {
                blinkMeshRef.current = child;
                blinkTargetIdxRef.current = child.morphTargetDictionary[key];
                break;
              }
            }
          }

          // Smart lighting filter for hair vs face skin
          if (child.isMesh && child.material) {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            materials.forEach((mat) => {
              const isHair = child.name.toLowerCase().includes("hair") || (mat.name && mat.name.toLowerCase().includes("hair"));
              if (mat.emissive) {
                if (isHair) {
                  mat.emissive.setHex(0x000000); // 髪の白飛びを完全に防止
                } else {
                  mat.emissive.setHex(0x080808); // お髭影を柔らかくする補正
                }
              }
              if (mat.roughness !== undefined) mat.roughness = 0.9;
              if (mat.metalness !== undefined) mat.metalness = 0.0;
            });
          }
        });

        anchor.group.add(gltf.scene);

        if (gltf.animations.length > 0) {
          const mixer = new ThreeAnimationMixer(gltf.scene);
          mixerRef.current = mixer;
          actionsRef.current["idle"] = mixer.clipAction(gltf.animations[0]);
          actionsRef.current["talking"] = mixer.clipAction(gltf.animations[1] || gltf.animations[0]);
          actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[2] || gltf.animations[0]);

          activeActionRef.current = actionsRef.current["idle"];
          activeActionRef.current.play();
        }
      });

      // 💡 [UPDATE] Trigger Cyber Ink Effect upon Target Detection
      anchor.onTargetFound = () => {
        setSubtitle("召喚に成功しました。何か話しかけてください。");
        
        // Reset spawn parameters
        spawnProgressRef.current = 0;
        isSpawningRef.current = true;

        // Spark particles burst
        if (particlesRef.current) {
          particlesRef.current.material.opacity = 1.0;
          const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
          for (let i = 0; i < particleCount; i++) {
            posArr[i * 3] = 0;
            posArr[i * 3 + 1] = -0.2; // Start slightly below anchor line
            posArr[i * 3 + 2] = 0;
          }
          particlesRef.current.geometry.attributes.position.needsUpdate = true;
        }
      };

      anchor.onTargetLost = () => {
        setSubtitle("ターゲットを見失いました。");
        isSpawningRef.current = false;
        if (avatarSceneRef.current) {
          avatarSceneRef.current.scale.set(0, 0, 0); // Hide instantly on loss
        }
      };

      // Internal states for local rendering calculations
      const clock = new Clock();
      let blinkTimer = 0;
      let isBlinking = false;
      let blinkDuration = 0.14; // Speed of eyelid movement
      let nextBlinkTime = 2.0 + Math.random() * 4.0; // Random interval between 2-6 seconds

      renderer.setAnimationLoop(() => {
        const delta = clock.getDelta();
        const elapsedTime = clock.getElapsedTime();
        
        if (mixerRef.current) mixerRef.current.update(delta);
        
        // 💡 [NEW: SPAWN ANIMATION LOGIC] Smoothly ease model scaling and elevation
        if (isSpawningRef.current && avatarSceneRef.current) {
          if (spawnProgressRef.current < 1.0) {
            spawnProgressRef.current += delta * 1.8; // Reaches full form in ~0.5s
            const progress = Math.min(spawnProgressRef.current, 1.0);
            
            // Cubic out easing curve
            const easeOutCubic = 1 - Math.pow(1 - progress, 3);
            
            avatarSceneRef.current.scale.set(easeOutCubic, easeOutCubic, easeOutCubic);
          } else {
            isSpawningRef.current = false;
          }
        }

        // 💡 [NEW: BREATHING SIMULATION] Delicate sinusoidal hovering to add micro-life
        if (avatarSceneRef.current && !isSpawningRef.current && spawnProgressRef.current >= 1.0) {
          // Sinusoidal subtle idle float (Approx 1.2cm range)
          avatarSceneRef.current.position.y = Math.sin(elapsedTime * 1.8) * 0.012;
        }

        // 💡 [NEW: RANDOM BLINK LOGIC] Runs continuous intervals
        if (blinkMeshRef.current && blinkTargetIdxRef.current !== null) {
          blinkTimer += delta;
          if (!isBlinking && blinkTimer >= nextBlinkTime) {
            isBlinking = true;
            blinkTimer = 0;
          }
          if (isBlinking) {
            if (blinkTimer < blinkDuration) {
              const progress = blinkTimer / blinkDuration;
              const weight = Math.sin(progress * Math.PI); // Natural open-close curve
              blinkMeshRef.current.morphTargetInfluences[blinkTargetIdxRef.current] = weight;
            } else {
              blinkMeshRef.current.morphTargetInfluences[blinkTargetIdxRef.current] = 0;
              isBlinking = false;
              blinkTimer = 0;
              nextBlinkTime = 1.5 + Math.random() * 4.5; // Roll next blink timer
            }
          }
        }

        // 💡 [NEW: PARTICLES RUNTIME UPDATE] Fly outwards and dissolve
        if (particlesRef.current && particleVelocitiesRef.current) {
          const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
          const vels = particleVelocitiesRef.current;
          
          for (let i = 0; i < particleCount; i++) {
            posArr[i * 3] += vels[i * 3] * delta;
            posArr[i * 3 + 1] += vels[i * 3 + 1] * delta;
            posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
            
            // Apply slight mock gravity/friction pull to ink particles
            vels[i * 3 + 1] -= delta * 0.2;
          }
          particlesRef.current.geometry.attributes.position.needsUpdate = true;
          
          // Slowly fade out opacity over runtime frames
          if (particlesRef.current.material.opacity > 0) {
            particlesRef.current.material.opacity -= delta * 1.4;
          }
        }

        // REAL-TIME VOICE LIP-SYNC RUNTIME
        const audioInstance = audioInstanceRef.current;
        const isVoicePlaying = audioInstance && !audioInstance.paused;

        if (isVoicePlaying && analyserRef.current && freqDataRef.current && faceMeshRef.current && mouthTargetIdxRef.current !== null) {
          analyserRef.current.getByteFrequencyData(freqDataRef.current);
          let totalAmplitude = 0;
          for (let i = 0; i < freqDataRef.current.length; i++) {
            totalAmplitude += freqDataRef.current[i];
          }
          const averageVolume = totalAmplitude / freqDataRef.current.length;
          const morphWeight = Math.min((averageVolume / 110) * 1.5, 1.0);
          
          faceMeshRef.current.morphTargetInfluences[mouthTargetIdxRef.current] = morphWeight > 0.05 ? morphWeight : 0;
        } else if (faceMeshRef.current && mouthTargetIdxRef.current !== null) {
          faceMeshRef.current.morphTargetInfluences[mouthTargetIdxRef.current] = 0;
        }

        renderer.render(scene, camera);
      });
    };

    start();

    return () => {
      if (mindarThreeInstance) mindarThreeInstance.stop();
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

  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text = formData.get("message") as string;
    if (!text.trim()) return;

    e.currentTarget.reset();

    const audioInstance = audioInstanceRef.current;
    if (audioInstance) {
      audioInstance.pause();
      audioInstance.src = ""; 
      audioInstance.play().catch(() => {});
      initAudioPipeline(audioInstance);
    }

    setSubtitle("思考中...");
    setAiStatus("thinking");

    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, wallet_address: address || null }),
        });

        if (!response.ok) throw new Error("APIへの接続に失敗しました");

        const data = await response.json();
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

    setTimeout(() => {
      setSubtitle(`【本番フロントテスト】「${text}」を受信。`);
      setAiStatus("talking");
      setTimeout(() => {
        setSubtitle("次の指示を待っています。");
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
        
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/50 backdrop-blur-md p-3 rounded-xl text-white border border-white/10">
          <span className="text-xs font-semibold flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${aiStatus === "thinking" ? "bg-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-green-400 animate-ping" : "bg-blue-400"}`} />
            STATUS: {aiStatus.toUpperCase()}
          </span>
          <div className="flex gap-2">
            <button onClick={() => setAiStatus("idle")} className="text-[10px] bg-gray-700 px-2 py-1 rounded">Idle</button>
            <button onClick={() => setAiStatus("thinking")} className="text-[10px] bg-yellow-600 px-2 py-1 rounded">Think</button>
            <button onClick={() => setAiStatus("talking")} className="text-[10px] bg-green-600 px-2 py-1 rounded">Talk</button>
          </div>
        </div>

        <div className="w-full space-y-3 pointer-events-auto mb-4">
          <div className="bg-black/70 backdrop-blur-lg p-4 rounded-2xl text-white text-center min-h-[70px] flex items-center justify-center border border-white/10 shadow-xl">
            <p className="text-sm font-medium leading-relaxed transition-all duration-300">
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
              placeholder={isListening ? "声を聴いています..." : "AI人格にメッセージを送信..."} 
              className="flex-1 bg-black/80 text-white border border-white/15 rounded-xl px-4 py-3.5 focus:outline-none focus:border-purple-500 text-sm placeholder-gray-500 backdrop-blur-md"
            />
            <button 
              type="submit" 
              className="bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 text-white px-5 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-transform"
            >
              送信
            </button>
          </form>
        </div>

      </div>
    </>
  );
}