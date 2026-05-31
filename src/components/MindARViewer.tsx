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

  // 💡 [NEW] Audio Pipeline References for Smart Lip-Sync
  const audioInstanceRef = useRef<HTMLAudioElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const freqDataRef = useRef<Uint8Array | null>(null);
  
  // 💡 [NEW] References to track the avatar's face mesh and mouth morph shape
  const faceMeshRef = useRef<any>(null);
  const mouthTargetIdxRef = useRef<number | null>(null);

  // 1. Initialize Global Audio Instance on Mount (Prevents MediaElement Source duplication errors)
  useEffect(() => {
    audioInstanceRef.current = new Audio();
    return () => {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause();
        audioInstanceRef.current = null;
      }
    };
  }, []);

  // 2. Smoothly crossfade 3D model animations when AI state changes
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

  // 3. Initialize Web Speech API for voice recording
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

  // 💡 [NEW] Safe Audio Context unlocker function required for mobile browsers
  const initAudioPipeline = (audioInstance: HTMLAudioElement) => {
    if (!audioContextRef.current) {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioContextClass();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 32; // Small size is optimal for basic amplitude calculations
      
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

      const dracoLoader = new DRACOLoader();
      dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");

      const loader = new GLTFLoader();
      loader.setDRACOLoader(dracoLoader);

      // Load asset via cache buster v6
      loader.load("/avatar.glb?v=6", (gltf) => {
        gltf.scene.scale.set(1.0, 1.0, 1.0);
        gltf.scene.rotation.x = Math.PI / 2;

        gltf.scene.traverse((child: any) => {
          // 💡 [UPDATE: LIP-SYNC] Automatically locate the facial mesh and the 'Aa' vowel morph target
          if (child.isMesh && child.morphTargetDictionary) {
            // Scan common VRoid shape key layouts for mouth opening profiles
            const candidates = ["aa", "Fcl_Mth_A", "Mouth_A", "A", "Oto_A"];
            for (const key of candidates) {
              if (child.morphTargetDictionary[key] !== undefined) {
                faceMeshRef.current = child;
                mouthTargetIdxRef.current = child.morphTargetDictionary[key];
                break;
              }
            }
          }

          // 💡 [UPDATE: LIGHTING] Smart emissive filtration to fix hair over-brightness
          if (child.isMesh && child.material) {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            materials.forEach((mat) => {
              // Discern if the mesh element corresponds to a hair configuration
              const isHair = child.name.toLowerCase().includes("hair") || (mat.name && mat.name.toLowerCase().includes("hair"));
              
              if (mat.emissive) {
                if (isHair) {
                  mat.emissive.setHex(0x000000); // 髪の毛パーツは完全に発光を切り、白飛びを防止
                } else {
                  mat.emissive.setHex(0x080808); // 肌や服は薄く発光させ、パキッとした黒いお髭影を防御
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

      anchor.onTargetFound = () => setSubtitle("召喚に成功しました。何か話しかけてください。");
      anchor.onTargetLost = () => setSubtitle("ターゲットを見失いました。");

      const clock = new Clock();
      await mindarThree.start();

      renderer.setAnimationLoop(() => {
        const delta = clock.getDelta();
        if (mixerRef.current) mixerRef.current.update(delta);
        
        // 💡 [UPDATE: REAL-TIME LIP-SYNC RUNTIME]
        const audioInstance = audioInstanceRef.current;
        const isVoicePlaying = audioInstance && !audioInstance.paused;

        if (isVoicePlaying && analyserRef.current && freqDataRef.current && faceMeshRef.current && mouthTargetIdxRef.current !== null) {
          analyserRef.current.getByteFrequencyData(freqDataRef.current);
          
          // Calculate average amplitude across captured frames
          let totalAmplitude = 0;
          for (let i = 0; i < freqDataRef.current.length; i++) {
            totalAmplitude += freqDataRef.current[i];
          }
          const averageVolume = totalAmplitude / freqDataRef.current.length;
          
          // Map voice amplitude to morph weights, scaling up slightly for visual impact
          const morphWeight = Math.min((averageVolume / 110) * 1.5, 1.0);
          
          // Inject real-time values into the Three.js mesh's morph array
          faceMeshRef.current.morphTargetInfluences[mouthTargetIdxRef.current] = morphWeight > 0.05 ? morphWeight : 0;
        } else if (faceMeshRef.current && mouthTargetIdxRef.current !== null) {
          // Snap mouth shut when audio playback terminates or pauses
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

    // 💡 Unlock audio context within the user's explicit tap scope
    const audioInstance = audioInstanceRef.current;
    if (audioInstance) {
      audioInstance.pause();
      audioInstance.src = ""; // Flush previous track references
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

    // Local standard mock fallback context
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