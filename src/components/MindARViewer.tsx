"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

type AIStatus = "idle" | "thinking" | "talking";
type SearchPhase = "OFFLINE" | "STABLE" | "CONNECTING..." | "TAVILY_SEARCHING..." | "DATA_ANALYZING...";

interface MorphTargetRef {
  mesh: any;
  idxs: number[];
}

interface HistoryItem {
  role: "user" | "ruki";
  text: string;
  timestamp: string;
}

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [searchPhase, setSearchPhase] = useState<SearchPhase>("STABLE"); 
  const [spatialEffect, setSpatialEffect] = useState<string>("cyber"); // 桜, 雪, 雨, サイバーの同期
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");
  const [isListening, setIsListening] = useState<boolean>(false);
  const [currentDateTime, setCurrentDateTime] = useState<string>(""); 
  const [isTargetFound, setIsTargetFound] = useState<boolean>(false); 

  const [chatHistory, setChatHistory] = useState<HistoryItem[]>([]); 
  const [isHistoryOpen, setIsHistoryOpen] = useState<boolean>(false); 
  const lostTimeoutRef = useRef<NodeJS.Timeout | null>(null); 

  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { address } = useAccount();
  const timersRef = useRef<NodeJS.Timeout[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // リアルタイムエフェクト同期用Ref
  const currentEffectRef = useRef<string>("cyber");

  // Three.js アニメーション関連
  const mixerRef = useRef<AnimationMixer | null>(null);
  const actionsRef = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);

  // オーディオ & リップシンク関連
  const audioInstanceRef = useRef<HTMLAudioElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const freqDataRef = useRef<Uint8Array | null>(null);
  
  const mouthTargetsRef = useRef<MorphTargetRef[]>([]);
  const blinkTargetsRef = useRef<MorphTargetRef[]>([]);
  const avatarSceneRef = useRef<any>(null);

  // パーティクル演出関連
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

  // 1. オーディオインスタンスの初期化
  useEffect(() => {
    audioInstanceRef.current = new Audio();
    return () => {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause();
        audioInstanceRef.current = null;
      }
      timersRef.current.forEach(clearTimeout);
      if (lostTimeoutRef.current) clearTimeout(lostTimeoutRef.current);
    };
  }, []);

  // 2. アニメーションクロスフェード制御
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

  // 3. Web Speech API (音声認識) の初期化
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

  // 📡【ルキルキ常時脳内同期用 WebSocket パイプライン】
  useEffect(() => {
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!baseUrl) return;

    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/avatar";
    let socket: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connectWebSocket = () => {
      console.log(`📡 [空間同期リンク] 接続開始: ${wsUrl}`);
      socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onopen = () => {
        console.log("✨ [空間同期リンク] ルキルキとの常時接続（脳内リンク）が成功しました！");
      };

      socket.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);
          
          // 空間エフェクト指示のリアルタイムハッキング
          if (data.spatial_effect) {
            setSpatialEffect(data.spatial_effect);
            currentEffectRef.current = data.spatial_effect;
          }

          if (data.type === "proactive_speech") {
            console.log("🗣️ [ルキルキ自発的発話] 脳内情報調査部からの報告を受信:", data.reply);
            if (audioInstanceRef.current) {
              audioInstanceRef.current.pause();
              audioInstanceRef.current.src = "";
            }
            timersRef.current.forEach(clearTimeout);
            timersRef.current = [];

            const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            setSubtitle(data.reply);
            setChatHistory(prev => [...prev, { role: "ruki", text: data.reply, timestamp: timeStampStr }]);

            if (data.audio_data && audioInstanceRef.current) {
              try {
                const binaryString = window.atob(data.audio_data);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
                const audioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));

                audioInstanceRef.current.onended = () => { 
                  setAiStatus("idle"); 
                  URL.revokeObjectURL(audioUrl); 
                };
                initAudioPipeline(audioInstanceRef.current);
                audioInstanceRef.current.src = audioUrl;
                setAiStatus("talking");
                await audioInstanceRef.current.play();
              } catch (audioErr) {
                console.error("自発的発話の音声生成に失敗:", audioErr);
                setAiStatus("talking");
                setTimeout(() => setAiStatus("idle"), 5000);
              }
            } else {
              setAiStatus("talking");
              setTimeout(() => setAiStatus("idle"), 5000);
            }
          }
        } catch (err) {
          console.error("WSメッセージのリアルタイムパースに失敗:", err);
        }
      };

      socket.onclose = () => {
        console.log("🍂 [空間同期リンク] 切断。5秒後に再接続を試みます。");
        reconnectTimeout = setTimeout(connectWebSocket, 5000);
      };

      socket.onerror = (error) => {
        console.error("⚠️ WebSocketエラー:", error);
      };
    };

    connectWebSocket();

    return () => {
      if (socket) socket.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []);

  // 4. MindAR & Three.js メイン初期化
  useEffect(() => {
    let mindarThreeInstance: any = null;
    let localRenderer: any = null; 

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
        localRenderer = renderer; 

        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.0; 

        // 🔥【超重要】カメラ映像透過バグの修正。クリアアルファを0にして透過
        renderer.setClearColor(0x000000, 0);

        const ambientLight = new THREE.AmbientLight(0xffffff, 1.2); 
        scene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.6); 
        directionalLight.position.set(0, 2, 10); 
        scene.add(directionalLight);

        const anchor = mindarThree.addAnchor(0);

        // クチュール・パーティクルジオメトリ生成
        const particleCount = 120; // 密度を少し強化
        const particleGeometry = new THREE.BufferGeometry();
        const particlePositions = new Float32Array(particleCount * 3);
        const particleVelocities = new Float32Array(particleCount * 3);

        for (let i = 0; i < particleCount; i++) {
          particlePositions[i * 3] = (Math.random() - 0.5) * 0.4;
          particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 0.4;
          particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 0.4;
          particleVelocities[i * 3] = (Math.random() - 0.5) * 0.4;
          particleVelocities[i * 3 + 1] = Math.random() * 0.6 + 0.2; 
          particleVelocities[i * 3 + 2] = (Math.random() - 0.5) * 0.4;
        }

        particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
        const particleMaterial = new THREE.PointsMaterial({
          color: 0x8b5cf6, size: 0.028, transparent: true, opacity: 0, blending: THREE.AdditiveBlending
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
                if (lowKey === "blink" || lowKey === "eyeblink" || lowKey === "close" || lowKey.includes("eye_close") || lowKey.includes("blink_")) {
                  bIdxs.push(child.morphTargetDictionary[key]);
                }
                if (lowKey === "aa" || lowKey === "a" || lowKey === "vowel_a" || lowKey.includes("mouth_a")) {
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
                if (mat.emissive) mat.emissive.setHex(isHair ? 0x000000 : 0x080808);
                if (mat.roughness !== undefined) mat.roughness = 0.9;
                if (mat.metalness !== undefined) mat.metalness = 0.0;
              });
            }
          });

          blinkTargetsRef.current = localBlinkTargets;
          mouthTargetsRef.current = localMouthTargets;
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
        });

        anchor.onTargetFound = () => {
          if (lostTimeoutRef.current) {
            clearTimeout(lostTimeoutRef.current);
            lostTimeoutRef.current = null;
            console.log("[XRシステム] 手ブレ境界線を検知。セッションをシームレスに復帰します。");
          }

          setIsTargetFound(true); 
          setSubtitle(prev => 
            prev === "（カメラをターゲットにかざしてください）" 
              ? "ルキルキを現実世界に固定しました。話しかけてください。" 
              : prev
          );

          spawnProgressRef.current = 0;
          isSpawningRef.current = true;

          if (particlesRef.current) {
            (particlesRef.current.material as any).opacity = 1.0;
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            for (let i = 0; i < particleCount; i++) {
              posArr[i * 3] = (Math.random() - 0.5) * 0.2; 
              posArr[i * 3 + 1] = -0.2; 
              posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.2;
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
          }
        };

        anchor.onTargetLost = () => {
          if (lostTimeoutRef.current) clearTimeout(lostTimeoutRef.current);
          console.log("[XRシステム] ターゲットロスト。残像ホールドシーケンスを開始（4000ms）");

          lostTimeoutRef.current = setTimeout(() => {
            setIsTargetFound(false); 
            setSubtitle("（カメラをターゲットにかざしてください）");
            isSpawningRef.current = false;
            if (avatarSceneRef.current) avatarSceneRef.current.scale.set(0, 0, 0); 
            
            if (recognitionRef.current) {
              try { recognitionRef.current.stop(); } catch(e){}
            }
            lostTimeoutRef.current = null;
            console.log("[XRシステム] 完全にロストしました。");
          }, 4000); 
        };

        const clock = new Clock();
        let blinkTimer = 0, isBlinking = false, blinkDuration = 0.14, nextBlinkTime = 2.0 + Math.random() * 4.0; 

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

          // まばたき制御
          if (blinkTargetsRef.current.length > 0) {
            blinkTimer += delta;
            if (!isBlinking && blinkTimer >= nextBlinkTime) { isBlinking = true; blinkTimer = 0; }
            if (isBlinking) {
              if (blinkTimer < blinkDuration) {
                const weight = Math.sin((blinkTimer / blinkDuration) * Math.PI); 
                blinkTargetsRef.current.forEach(t => t.idxs.forEach(idx => t.mesh.morphTargetInfluences[idx] = weight));
              } else {
                blinkTargetsRef.current.forEach(t => t.idxs.forEach(idx => t.mesh.morphTargetInfluences[idx] = 0));
                isBlinking = false; blinkTimer = 0; nextBlinkTime = 1.5 + Math.random() * 4.5;
              }
            }
          }

          // 🔮【新設】環境適応型パーティクル物理シミュレーションループ
          if (particlesRef.current && particleVelocitiesRef.current) {
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            const vels = particleVelocitiesRef.current;
            const currentEffect = currentEffectRef.current;
            const mat = particlesRef.current.material as any;

            // 情緒パラメータに合わせたマテリアルカラー変更
            if (currentEffect === "sakura") mat.color.setHex(0xfbcfe8);      // 桜：薄桃
            else if (currentEffect === "snow") mat.color.setHex(0xffffff);  // 雪：純白
            else if (currentEffect === "rain") mat.color.setHex(0x3b82f6);  // 雨：蒼
            else mat.color.setHex(0x8b5cf6);                                // デフォルト：サイバー紫

            for (let i = 0; i < particleCount; i++) {
              if (currentEffect === "sakura") {
                // ひらひらと斜めに舞い散る風の動き
                posArr[i * 3] += vels[i * 3] * delta * 0.4 + Math.sin(elapsedTime + i) * 0.002;
                posArr[i * 3 + 1] -= Math.abs(vels[i * 3 + 1]) * delta * 0.3;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.4 + Math.cos(elapsedTime + i) * 0.002;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (currentEffect === "snow") {
                // 静かに垂直降下
                posArr[i * 3] += vels[i * 3] * delta * 0.1;
                posArr[i * 3 + 1] -= 0.12 * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.1;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (currentEffect === "rain") {
                // 高速で地面へ突き刺さる
                posArr[i * 3 + 1] -= 1.9 * delta;
                if (posArr[i * 3 + 1] < -0.4) {
                  posArr[i * 3 + 1] = 0.4;
                  posArr[i * 3] = (Math.random() - 0.5) * 0.3;
                }
              } else {
                // 通常サイバー：下から湧き上がるホログラム粒子
                posArr[i * 3] += vels[i * 3] * delta; 
                posArr[i * 3 + 1] += vels[i * 3 + 1] * delta; 
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
                vels[i * 3 + 1] -= delta * 0.2; 
              }
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;

            if (isSpawningRef.current) {
              if (mat.opacity < 0.9) mat.opacity += delta * 2.0;
            } else {
              if (["sakura", "snow", "rain"].includes(currentEffect)) {
                mat.opacity = 0.75; // 環境エフェクト時は常時維持
              } else if (mat.opacity > 0) {
                mat.opacity -= delta * 1.2;
              }
            }
          }

          // リップシンク処理
          const audioInstance = audioInstanceRef.current;
          if (audioInstance && !audioInstance.paused && analyserRef.current && freqDataRef.current && mouthTargetsRef.current.length > 0) {
            analyserRef.current.getByteFrequencyData(freqDataRef.current);
            let totalAmplitude = 0;
            for (let i = 0; i < freqDataRef.current.length; i++) totalAmplitude += freqDataRef.current[i];
            const morphWeight = Math.min(((totalAmplitude / freqDataRef.current.length) / 110) * 1.5, 1.0);
            const finalWeight = morphWeight > 0.05 ? morphWeight : 0;
            mouthTargetsRef.current.forEach(t => t.idxs.forEach(idx => t.mesh.morphTargetInfluences[idx] = finalWeight));
          } else if (mouthTargetsRef.current.length > 0) {
            mouthTargetsRef.current.forEach(t => t.idxs.forEach(idx => t.mesh.morphTargetInfluences[idx] = 0));
          }

          renderer.render(scene, camera);
        });

      } catch (initError: any) {
        console.error("MindAR起動失敗:", initError);
        setSubtitle(`システム初期化エラー: ${initError?.message || String(initError)}`);
      }
    };

    start();
    
    return () => { 
      if (localRenderer) { try { localRenderer.setAnimationLoop(null); } catch(e){} }
      if (mindarThreeInstance) { try { mindarThreeInstance.stop(); } catch(e){} } 
    };
  }, []);

  const getGPSLocation = (): Promise<{ lat: number; lng: number } | null> => {
    return new Promise((resolve) => {
      if (!navigator.geolocation) { resolve(null); return; }
      navigator.geolocation.getCurrentPosition(
        (position) => resolve({ lat: position.coords.latitude, lng: position.coords.longitude }),
        () => resolve(null),
        { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 }
      );
    });
  };

  const captureARCameraFrame = (): string | null => {
    const video = containerRef.current?.querySelector("video");
    if (!video || video.videoWidth === 0) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  };

  const toggleListening = () => {
    if (!recognitionRef.current) return;
    if (isListening) {
      recognitionRef.current.stop();
    } else {
      if (audioInstanceRef.current) { audioInstanceRef.current.pause(); audioInstanceRef.current.src = ""; }
      if (audioContextRef.current) audioContextRef.current.resume();
      recognitionRef.current.start();
    }
  };

  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text = formData.get("message") as string;
    if (!text.trim()) return;

    if (inputRef.current) inputRef.current.blur();
    setTimeout(() => { window.scrollTo({ top: 0, left: 0, behavior: "smooth" }); }, 100);

    if (audioInstanceRef.current) { audioInstanceRef.current.pause(); audioInstanceRef.current.src = ""; }
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];

    setSubtitle(`思考中... 「${text}」`);
    setAiStatus("thinking");
    setSearchPhase("CONNECTING..."); 

    const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    setChatHistory(prev => [...prev, { role: "user", text, timestamp: timeStampStr }]);

    timersRef.current.push(
      setTimeout(() => { setSearchPhase("TAVILY_SEARCHING..."); setSubtitle(`🌐 外部情報空間を走査中...\n（Tavilyサーチを同期しています）`); }, 1800),
      setTimeout(() => { setSearchPhase("DATA_ANALYZING..."); setSubtitle(`🔮 取得した時間軸データを展開中...\n（ルキルキが回答を再構成しています）`); }, 5000)
    );

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
            longitude: location ? location.lng : null,
            history: chatHistory 
          }),
        });

        timersRef.current.forEach(clearTimeout); timersRef.current = [];
        setSearchPhase("STABLE");
        if (!response.ok) throw new Error("API接続エラー");

        const data = await response.json();
        if (inputRef.current) inputRef.current.value = "";
        
        // HTTP応答からエフェクト指令を取り出して物理エンジンに即時同期
        if (data.spatial_effect) {
          setSpatialEffect(data.spatial_effect);
          currentEffectRef.current = data.spatial_effect;
        }

        setSubtitle(data.reply);
        setChatHistory(prev => [...prev, { role: "ruki", text: data.reply, timestamp: timeStampStr }]);

        if (data.audio_data && audioInstanceRef.current) {
          try {
            const binaryString = window.atob(data.audio_data);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
            const audioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));

            audioInstanceRef.current.onended = () => { setAiStatus("idle"); URL.revokeObjectURL(audioUrl); };
            initAudioPipeline(audioInstanceRef.current);
            audioInstanceRef.current.src = audioUrl;
            setAiStatus("talking");
            await audioInstanceRef.current.play();
          } catch {
            setAiStatus("talking"); setTimeout(() => setAiStatus("idle"), 5000);
          }
        } else {
          setAiStatus("talking"); setTimeout(() => setAiStatus("idle"), 5000);
        }
        return;
      } catch {
        timersRef.current.forEach(clearTimeout); setSearchPhase("OFFLINE");
        setSubtitle("バックエンドとの通信に失敗しました。");
        setAiStatus("idle");
        return;
      }
    }
  };

  const isControlDisabled = !isTargetFound || aiStatus === "thinking";

  return (
    <>
      {/* 💥 レイヤー不透過バグをCSSでも徹底破壊 */}
      <style dangerouslySetInnerHTML={{ __html: `
        .mindar-full-container video {
          width: 100vw !important; height: 100vh !important; object-fit: cover !important;
          position: fixed !important; top: 0 !important; left: 0 !important;
          z-index: 1 !important;
        }
        .mindar-full-container canvas {
          width: 100vw !important; height: 100vh !important; object-fit: cover !important;
          position: fixed !important; top: 0 !important; left: 0 !important;
          z-index: 2 !important;
        }
        @keyframes cyber-scan { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
        .animate-cyber-scan { animation: cyber-scan 1.5s infinite linear; }
      `}} />

      {/* コンテナ自体は完全に透過。背景黒を撤廃 */}
      <div ref={containerRef} className="mindar-full-container" style={{ position: "fixed", top: 0, left: 0, width: "100vw", height: "100vh", overflow: "hidden", zIndex: 1 }} />

      {/* UIコンソールレイヤー(z-50)で描画キャンバスの前面に固定 */}
      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-mono select-none">
        {/* 上部ヘッダーエリア */}
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/60 backdrop-blur-md p-3 rounded-xl text-white border border-purple-500/30 shadow-[0_0_15px_rgba(139,92,246,0.2)]">
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] text-purple-400 font-bold tracking-widest">OBSERVATION SYSTEM v3.5</span>
            <span className="text-xs font-semibold flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${aiStatus === "thinking" ? "bg-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-cyan-400 animate-ping" : "bg-purple-500"}`} />
              STATUS: <span className={aiStatus === "thinking" ? "text-yellow-400" : aiStatus === "talking" ? "text-cyan-400" : "text-purple-400"}>{aiStatus.toUpperCase()}</span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden sm:flex flex-col items-end text-right text-[9px] mr-1">
              <span className="text-gray-500">SPATIAL_ENV</span>
              <span className="font-bold text-emerald-400">[{spatialEffect.toUpperCase()}]</span>
            </div>
            
            <button
              onClick={() => setIsHistoryOpen(!isHistoryOpen)}
              className="text-xs font-mono font-bold text-purple-400 bg-purple-950/50 border border-purple-500/40 hover:bg-purple-900/60 px-3 py-1 rounded-md active:scale-95 transition-all shadow-[0_0_8px_rgba(168,85,247,0.2)]"
            >
              📜 LOG ({chatHistory.length})
            </button>

            <span className="text-xs font-mono text-cyan-400 bg-black/40 border border-cyan-500/20 px-2 py-1 rounded-md">{currentDateTime}</span>
          </div>
        </div>

        {/* 下部 UI エリア */}
        <div className="w-full space-y-3 pointer-events-auto mb-4 max-w-2xl mx-auto">
          {/* 字幕ボード */}
          <div className="relative bg-black/75 backdrop-blur-xl p-5 rounded-xl text-white min-h-[85px] flex flex-col items-center justify-center border border-purple-500/20 shadow-[0_4px_20px_rgba(0,0,0,0.6)] overflow-hidden">
            {aiStatus === "thinking" && (
              <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-cyan-400 to-purple-500 overflow-hidden">
                <div className="w-1/2 h-full bg-gradient-to-r from-cyan-400 to-purple-400 animate-cyber-scan" />
              </div>
            )}
            <div className="absolute top-1.5 left-3 text-[8px] text-gray-500 tracking-wider flex gap-2">
              <span>[SUBTITLE_OUTPUT]</span>
              {searchPhase !== "STABLE" && <span className="text-yellow-500 animate-pulse">⚡ LINKING_TAVILY</span>}
            </div>
            <p className="text-sm font-medium leading-relaxed text-center px-2 mt-1 whitespace-pre-line">{subtitle}</p>
          </div>

          {/* 入力コンソールフォーム */}
          <form onSubmit={handleSendMessage} className="flex gap-2">
            <button
              type="button"
              onClick={toggleListening}
              disabled={isControlDisabled}
              className={`px-4 py-3.5 rounded-xl font-semibold text-sm shadow-lg active:scale-95 transition-all pointer-events-auto border disabled:opacity-30 disabled:cursor-not-allowed ${
                isListening 
                  ? "bg-red-600/20 border-red-500 text-red-400 shadow-[0_0_15px_rgba(239,68,68,0.4)] animate-pulse" 
                  : "bg-black/60 text-gray-300 border-purple-500/20 hover:bg-purple-950/20"
              }`}
            >
              {isListening ? "🛰️" : "🎙️"}
            </button>

            <div className="relative flex-1 flex items-center">
              <input 
                ref={inputRef}
                type="text" 
                name="message"
                disabled={isControlDisabled}
                placeholder={
                  !isTargetFound 
                    ? "::: ターゲットを見失っています :::" 
                    : isListening ? "::: 空間音響データをスキャン中 :::" : "まがとき教授、ルキルキへコマンドを入力..."
                } 
                className="w-full bg-black/80 text-white border border-purple-500/20 rounded-xl px-4 py-3.5 focus:outline-none focus:border-cyan-500/60 text-sm placeholder-gray-600 backdrop-blur-md disabled:opacity-30 disabled:cursor-not-allowed shadow-[inset_0_1px_4px_rgba(0,0,0,0.8)]"
              />
              <div className={`absolute right-3 w-1.5 h-1.5 rounded-full ${!isTargetFound ? "bg-gray-700" : aiStatus === "thinking" ? "bg-yellow-400 animate-ping" : "bg-purple-500"}`} />
            </div>

            <button 
              type="submit" 
              disabled={isControlDisabled}
              className="bg-gradient-to-r from-purple-700 via-indigo-700 to-cyan-700 hover:from-purple-600 hover:to-cyan-600 text-white px-5 py-3.5 rounded-xl font-bold text-sm shadow-[0_0_15px_rgba(109,40,217,0.3)] active:scale-95 transition-all disabled:opacity-30 disabled:pointer-events-none tracking-widest border border-white/10"
            >
              送信
            </button>
          </form>
        </div>
      </div>

      {/* バックログ履歴モーダル */}
      {isHistoryOpen && (
        <div className="fixed inset-0 bg-black/85 backdrop-blur-md z-50 flex flex-col p-6 font-mono text-white pointer-events-auto">
          <div className="flex justify-between items-center border-b border-purple-500/30 pb-3 mb-4">
            <div className="flex flex-col">
              <span className="text-purple-400 font-bold tracking-widest text-sm">::: RUKIRUKI_MISSION_LOG_RECORDER :::</span>
              <span className="text-[9px] text-gray-500">MAGATOKI LAB CORE MEMORY SYSTEM</span>
            </div>
            <button 
              onClick={() => setIsHistoryOpen(false)}
              className="text-xs bg-purple-950/60 border border-purple-500/40 text-purple-300 px-4 py-1.5 rounded-md hover:bg-purple-900/60 transition-colors font-bold"
            >
              CLOSE [X]
            </button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-4 pr-2 scrollbar-thin">
            {chatHistory.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-500 text-xs py-12 gap-2">
                <span>─── 観測ログ履歴データが空です ───</span>
              </div>
            ) : (
              chatHistory.map((item, idx) => (
                <div 
                  key={idx} 
                  className={`p-3.5 rounded-xl border text-xs leading-relaxed shadow-md ${
                    item.role === "user" 
                      ? "bg-cyan-950/20 border-cyan-500/30 ml-12" 
                      : "bg-purple-950/20 border-purple-500/30 mr-12"
                  }`}
                >
                  <div className="flex justify-between items-center mb-1.5 text-[10px] font-bold">
                    <span className={item.role === "user" ? "text-cyan-400" : "text-purple-400"}>
                      {item.role === "user" ? "▶ まがとき教授" : "◁ ルキルキ SYSTEM"}
                    </span>
                    <span className="text-gray-500 font-normal">{item.timestamp}</span>
                  </div>
                  <p className={item.role === "user" ? "text-cyan-100" : "text-purple-100"}>{item.text}</p>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </>
  );
}