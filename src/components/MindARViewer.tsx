"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

// Define AI status types
type AIStatus = "idle" | "thinking" | "talking";

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  // React state management for AI personality
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");

  // Voice recognition states and references
  const [isListening, setIsListening] = useState<boolean>(false);
  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Retrieve the connected wallet address via wagmi
  const { address } = useAccount();

  // References for Three.js animation control
  const mixerRef = useRef<AnimationMixer | null>(null);
  const actionsRef = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);

  // Reference for managing active ElevenLabs/OpenAI audio playbacks
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // 1. Smoothly crossfade 3D model animations when AI state changes
  useEffect(() => {
    const fadeToAction = (status: AIStatus, duration: number = 0.5) => {
      const nextAction = actionsRef.current[status];
      const currentAction = activeActionRef.current;

      if (!nextAction || nextAction === currentAction) return;

      // Fade in the new motion state
      nextAction.reset();
      nextAction.setEffectiveTimeScale(1);
      nextAction.setEffectiveWeight(1);
      nextAction.fadeIn(duration);
      nextAction.play();

      // Fade out the previous motion state
      if (currentAction) {
        currentAction.fadeOut(duration);
      }

      // Track the current active action
      activeActionRef.current = nextAction;
    };

    fadeToAction(aiStatus);
  }, [aiStatus]);

  // 2. Initialize Web Speech API for native browser speech-to-text conversion
  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = false; // Stop listening automatically when the user pauses
      recognition.lang = "ja-JP";     // Optimize engine for Japanese speech patterns
      recognition.interimResults = false; // Capture only final processed results

      recognition.onstart = () => {
        setIsListening(true);
        setSubtitle("（音声認識中...お話しください）");
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognition.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        if (inputRef.current) {
          // Fill input box with recognized voice text
          inputRef.current.value = transcript;
          
          // Programmatically submit the form for a hands-free interactive experience
          const form = inputRef.current.form;
          if (form) form.requestSubmit();
        }
      };

      recognitionRef.current = recognition;
    }
  }, []);

  // 3. Initialize MindAR and Three.js environment
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

      // 💡 [UPDATE] Enhanced Studio Lighting Setup to fix pitch-black avatar shadows
      const ambientLight = new THREE.AmbientLight(0xffffff, 1.5); // Soft white ambient light
      scene.add(ambientLight);

      const directionalLight = new THREE.DirectionalLight(0xffffff, 1.5); // Key directional light
      directionalLight.position.set(0, 10, 10);
      scene.add(directionalLight);

      const hemisphereLight = new THREE.HemisphereLight(0xffffff, 0xbbbbff, 0.5); // Sky/ground context light
      scene.add(hemisphereLight);

      const anchor = mindarThree.addAnchor(0);

      // Setup DRACO decoder for compressed meshes
      const dracoLoader = new DRACOLoader();
      dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");

      const loader = new GLTFLoader();
      loader.setDRACOLoader(dracoLoader);

      // Load avatar asset and extract key skeletal animations with cache buster v2
      loader.load("/avatar.glb?v=2", (gltf) => {
        // 💡 [UPDATE] Magnify avatar scale by exactly 3x (from 0.3 to 0.9)
        gltf.scene.scale.set(0.9, 0.9, 0.9);

        // 💡 [UPDATE] Rotate avatar 90 degrees on X-axis to stand up straight relative to the image target
        // (Note: If it stands upside down or backwards, adjust to -Math.PI / 2 or add Y-axis rotation)
        gltf.scene.rotation.x = Math.PI / 2;

        anchor.group.add(gltf.scene);

        if (gltf.animations.length > 0) {
          const mixer = new ThreeAnimationMixer(gltf.scene);
          mixerRef.current = mixer;

          // Map specific animations to appropriate state targets
          actionsRef.current["idle"] = mixer.clipAction(gltf.animations[0]);
          actionsRef.current["talking"] = mixer.clipAction(gltf.animations[1] || gltf.animations[0]);
          actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[2] || gltf.animations[0]);

          // Trigger initial default idling animation loop
          activeActionRef.current = actionsRef.current["idle"];
          activeActionRef.current.play();
        }
      });

      // Target marker visibility triggers
      anchor.onTargetFound = () => {
        setSubtitle("召喚に成功しました。何か話しかけてください。");
      };
      anchor.onTargetLost = () => {
        setSubtitle("ターゲットを見失いました。");
      };

      const clock = new Clock();
      await mindarThree.start();

      renderer.setAnimationLoop(() => {
        const delta = clock.getDelta();
        if (mixerRef.current) mixerRef.current.update(delta);
        renderer.render(scene, camera);
      });
    };

    start();

    // Cleanup assets and stop streams upon component unmounting
    return () => {
      if (mindarThreeInstance) {
        mindarThreeInstance.stop();
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  // Trigger microphone capture interface
  const toggleListening = () => {
    if (!recognitionRef.current) {
      alert("お使いのブラウザは音声認識に対応していません。ChromeかSafariでお試しください。");
      return;
    }

    if (isListening) {
      recognitionRef.current.stop();
    } else {
      // Fires browser micro-permission request popup upon first active trigger
      recognitionRef.current.start();
    }
  };

  // Dispatch payloads to the backend API layer
  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text = formData.get("message") as string;
    if (!text.trim()) return;

    e.currentTarget.reset();

    // Kill any existing playback immediately if a new user phrase intercepts
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }

    // Switch state to thinking triggers
    setSubtitle("思考中...");
    setAiStatus("thinking");

    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    // Pattern A: Dispatch to live functional backend API endpoint
    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: text,
            wallet_address: address || null,
          }),
        });

        if (!response.ok) {
          throw new Error("APIへの接続に失敗しました");
        }

        const data = await response.json();
        setSubtitle(data.reply);

        // Process audio response streaming payload arrays
        if (data.audio_data) {
          try {
            const audioSrc = `data:audio/mpeg;base64,${data.audio_data}`;
            const audio = new Audio(audioSrc);
            audioRef.current = audio;

            // Trigger talking state animations
            setAiStatus("talking");
            await audio.play();

            // Smooth reset back into idle loop precisely upon phrase resolution ends
            audio.onended = () => {
              setSubtitle("次の指示を待っています。");
              setAiStatus("idle");
              audioRef.current = null;
            };
          } catch (audioError) {
            console.error("音声の再生に失敗しました。フォールバックタイマーに切り替えます:", audioError);
            setAiStatus("talking");
            setTimeout(() => {
              setSubtitle("次の指示を待っています.");
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

    // Pattern B: Local sandbox fallbacks when URL configurations are absent
    setTimeout(() => {
      setSubtitle(`【本番フロントテスト】「${text}」を受信。バックエンド未接続のため、フロント側で召喚を維持します。`);
      setAiStatus("talking");

      setTimeout(() => {
        setSubtitle("次の指示を待っています。");
        setAiStatus("idle");
      }, 5000);
    }, 2000);
  };

  return (
    <>
      {/* Structural full-screen view CSS updates */}
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

      {/* Camera viewpoint layout box */}
      <div
        ref={containerRef}
        className="mindar-full-container"
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          overflow: "hidden",
          zIndex: 1,
          backgroundColor: "#000",
        }}
      />

      {/* UI Interaction Layer */}
      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-sans">
        
        {/* Top: Status Tracking Bar */}
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

        {/* Bottom: Subtitle Box and Interactive Text/Voice Form Fields */}
        <div className="w-full space-y-3 pointer-events-auto mb-4">
          
          {/* Output Subtitles Box */}
          <div className="bg-black/70 backdrop-blur-lg p-4 rounded-2xl text-white text-center min-h-[70px] flex items-center justify-center border border-white/10 shadow-xl">
            <p className="text-sm font-medium leading-relaxed transition-all duration-300">
              {subtitle}
            </p>
          </div>

          {/* Interactive Core Form */}
          <form onSubmit={handleSendMessage} className="flex gap-2">
            {/* Dynamic Voice Recording Toggle Button */}
            <button
              type="button"
              onClick={toggleListening}
              className={`px-4 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-all pointer-events-auto ${
                isListening 
                  ? "bg-red-600 text-white animate-pulse" 
                  : "bg-gray-800 text-white border border-white/10 hover:bg-gray-700"
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