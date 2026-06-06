// frontend/components/MindARViewer.tsx
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
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");
  const [isListening, setIsListening] = useState<boolean>(false);
  const [currentDateTime, setCurrentDateTime] = useState<string>(""); 
  const [isTargetFound, setIsTargetFound] = useState<boolean>(false); 

  // 🌌 [新設] 自律空間エフェクト同期用のステート＆参照系
  const [spatialEffect, setSpatialEffect] = useState<string>("cyber_glow");
  const effectRef = useRef<string>("cyber_glow");
  const isEffectChangedRef = useRef<boolean>(false); // エフェクト切り替え時のリスポーン検知用

  const [chatHistory, setChatHistory] = useState<HistoryItem[]>([]); 
  const [isHistoryOpen, setIsHistoryOpen] = useState<boolean>(false); 
  const lostTimeoutRef = useRef<NodeJS.Timeout | null>(null); 

  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { address } = useAccount();
  const timersRef = useRef<NodeJS.Timeout[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const mixerRef = useRef<AnimationMixer | null>(null);
  const actionsRef = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);

  const audioInstanceRef = useRef<HTMLAudioElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const freqDataRef = useRef<Uint8Array | null>(null);
  
  const mouthTargetsRef = useRef<MorphTargetRef[]>([]);
  const blinkTargetsRef = useRef<MorphTargetRef[]>([]);
  const avatarSceneRef = useRef<any>(null);

  const particlesRef = useRef<any>(null);
  const particleVelocitiesRef = useRef<Float32Array | null>(null);
  const spawnProgressRef = useRef<number>(0);
  const isSpawningRef = useRef<boolean>(false);

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

  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.lang = "ja-JP";
      recognition.interimResults = false;
      recognition.onstart = () => { setIsListening(true); setSubtitle("（音声認識中...お話しください）"); };
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
    if (audioContextRef.current.state === "suspended") audioContextRef.current.resume();
  };

  // 📡 【常時脳内同期用 WebSocket パイプライン：自律エフェクトレシーバー拡張】
  useEffect(() => {
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!baseUrl) return;

    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/avatar";
    let socket: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connectWebSocket = () => {
      socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);
          
          // 💡 LLMが自律決定したエフェクトパケットをキャッチ
          if (data.spatial_effect || data.type === "spatial_effect") {
            const nextEffect = data.spatial_effect;
            console.log(`🌌 [空間演出ハック] LLMが選択した空間エフェクトを適用します: ${nextEffect}`);
            setSpatialEffect(nextEffect);
            effectRef.current = nextEffect;
            isEffectChangedRef.current = true; // アニメーションループ側へ再配置を要求
          }

          if (data.type === "proactive_speech") {
            if (audioInstanceRef.current) { audioInstanceRef.current.pause(); audioInstanceRef.current.src = ""; }
            timersRef.current.forEach(clearTimeout); timersRef.current = [];
            const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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
              } catch (e) {
                setAiStatus("talking"); setTimeout(() => setAiStatus("idle"), 5000);
              }
            } else {
              setAiStatus("talking"); setTimeout(() => setAiStatus("idle"), 5000);
            }
          }
        } catch (err) {
          console.error("WSパースエラー:", err);
        }
      };

      socket.onclose = () => { reconnectTimeout = setTimeout(connectWebSocket, 5000); };
    };

    connectWebSocket();
    return () => { if (socket) socket.close(); if (reconnectTimeout) clearTimeout(reconnectTimeout); };
  }, []);

  // 4. MindAR & Three.js メインロジック
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

        if (!containerRef.current) throw new Error("DOMコンテナ未見つからず");

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

        scene.add(new THREE.AmbientLight(0xffffff, 1.2));
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.6); 
        directionalLight.position.set(0, 2, 10); 
        scene.add(directionalLight);

        const anchor = mindarThree.addAnchor(0);

        // パーティクル生成（多用途に対応できるよう多めの120個に拡張）
        const particleCount = 120;
        const particleGeometry = new THREE.BufferGeometry();
        const particlePositions = new Float32Array(particleCount * 3);
        const particleVelocities = new Float32Array(particleCount * 3);

        const initParticles = (positions: Float32Array, velocities: Float32Array, currentType: string) => {
          for (let i = 0; i < particleCount; i++) {
            if (currentType === "cherry_blossom" || currentType === "snow_crystal" || currentType === "cyber_rain") {
              // 🌸❄️🌧️ 上から降らせるタイプ：最初からランダムな高さに分布させる
              positions[i * 3] = (Math.random() - 0.5) * 0.6;
              positions[i * 3 + 1] = Math.random() * 0.8;
              positions[i * 3 + 2] = (Math.random() - 0.5) * 0.6;
            } else {
              // ⚡🌀 下から立ち上るタイプ：足元に集約
              positions[i * 3] = (Math.random() - 0.5) * 0.2;
              positions[i * 3 + 1] = -0.2 + Math.random() * 0.1;
              positions[i * 3 + 2] = (Math.random() - 0.5) * 0.2;
            }
            
            // 初期基本速度の設定
            velocities[i * 3] = (Math.random() - 0.5) * 0.2;
            velocities[i * 3 + 1] = Math.random() * 0.4 + 0.1; 
            velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.2;
          }
        };

        initParticles(particlePositions, particleVelocities, "cyber_glow");

        particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
        const particleMaterial = new THREE.PointsMaterial({
          color: 0x8b5cf6, size: 0.035, transparent: true, opacity: 0, blending: THREE.AdditiveBlending
        });

        const spawnParticles = new THREE.Points(particleGeometry, particleMaterial);
        anchor.group.add(spawnParticles);
        particlesRef.current = spawnParticles;
        particleVelocitiesRef.current = particleVelocities;

        const dracoLoader = new DRACOLoader();
        dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");
        const loader = new GLTFLoader(); loader.setDRACOLoader(dracoLoader);

        const localBlinkTargets: MorphTargetRef[] = [];
        const localMouthTargets: MorphTargetRef[] = [];

        loader.load("/avatar.glb?v=9", (gltf) => {
          gltf.scene.scale.set(0, 0, 0); gltf.scene.rotation.x = Math.PI / 2;
          avatarSceneRef.current = gltf.scene;
          gltf.scene.traverse((child: any) => {
            if (child.isMesh && child.morphTargetDictionary) {
              const bIdxs: number[] = [], mIdxs: number[] = [];
              Object.keys(child.morphTargetDictionary).forEach((key) => {
                const lowKey = key.toLowerCase();
                if (lowKey.includes("blink") || lowKey.includes("eye_close")) bIdxs.push(child.morphTargetDictionary[key]);
                if (lowKey === "aa" || lowKey === "a" || lowKey.includes("mouth_a")) mIdxs.push(child.morphTargetDictionary[key]);
              });
              if (bIdxs.length > 0) localBlinkTargets.push({ mesh: child, idxs: bIdxs });
              if (mIdxs.length > 0) localMouthTargets.push({ mesh: child, idxs: mIdxs });
            }
          });
          blinkTargetsRef.current = localBlinkTargets; mouthTargetsRef.current = localMouthTargets;
          anchor.group.add(gltf.scene);

          if (gltf.animations.length > 0) {
            const mixer = new ThreeAnimationMixer(gltf.scene); mixerRef.current = mixer;
            actionsRef.current["idle"] = mixer.clipAction(gltf.animations[0]);
            actionsRef.current["talking"] = mixer.clipAction(gltf.animations[2] || gltf.animations[0]);
            actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[1] || gltf.animations[0]);
            activeActionRef.current = actionsRef.current["idle"]; activeActionRef.current.play();
          }
        });

        anchor.onTargetFound = () => {
          if (lostTimeoutRef.current) { clearTimeout(lostTimeoutRef.current); lostTimeoutRef.current = null; }
          setIsTargetFound(true);
          setSubtitle(prev => prev.includes("かざして") ? "ルキルキを現実世界に固定しました。" : prev);
          spawnProgressRef.current = 0; isSpawningRef.current = true;

          if (particlesRef.current) {
            (particlesRef.current.material as any).opacity = 1.0;
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            initParticles(posArr, particleVelocitiesRef.current!, effectRef.current);
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
          }
        };

        anchor.onTargetLost = () => {
          lostTimeoutRef.current = setTimeout(() => {
            setIsTargetFound(false); setSubtitle("（カメラをターゲットにかざしてください）");
            isSpawningRef.current = false; if (avatarSceneRef.current) avatarSceneRef.current.scale.set(0, 0, 0);
            if (recognitionRef.current) { try { recognitionRef.current.stop(); } catch(e){} }
            lostTimeoutRef.current = null;
          }, 4000);
        };

        const clock = new Clock();
        let blinkTimer = 0, isBlinking = false, blinkDuration = 0.14, nextBlinkTime = 2.0 + Math.random() * 4.0;

        await mindarThree.start();

        // 🔄 ─── 【メインレンダーループ：自律エフェクト動的シェーディングハック】 ───
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
            } else { isSpawningRef.current = false; }
          }
          if (avatarSceneRef.current && !isSpawningRef.current && spawnProgressRef.current >= 1.0) {
            avatarSceneRef.current.position.y = Math.sin(elapsedTime * 1.8) * 0.012;
          }

          // まばたき・リップシンク制御（既存流用）
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

          // 🌌 ─── パーティクル動的物理シェーディングエンジン ───
          if (particlesRef.current && particleVelocitiesRef.current) {
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            const vels = particleVelocitiesRef.current;
            const pMaterial = particlesRef.current.material as THREE.PointsMaterial;
            const currentType = effectRef.current;

            // 1. エフェクトIDが切り替わった瞬間に、マテリアルの基本プロパティと粒子位置を初期化
            if (isEffectChangedRef.current) {
              initParticles(posArr, vels, currentType);
              isEffectChangedRef.current = false;
              pMaterial.opacity = 1.0; // フェードをリセット
            }

            // 2. LLMの決定したエフェクトIDに応じて外観を動的設定
            if (currentType === "incense_smoke") {
              pMaterial.color.setHex(0xe0e7ff); // はんなり漂うお香の薄藤白
              pMaterial.size = 0.045;
              pMaterial.blending = THREE.NormalBlending; // 煙らしいソフトな重なり
            } else if (currentType === "cherry_blossom") {
              pMaterial.color.setHex(0xfbcfe8); // 古都の情緒を引き立てる桜ピンク
              pMaterial.size = 0.028;
              pMaterial.blending = THREE.AdditiveBlending;
            } else if (currentType === "snow_crystal") {
              pMaterial.color.setHex(0xe0f2fe); // しんしんと降る白銀スノー
              pMaterial.size = 0.025;
              pMaterial.blending = THREE.AdditiveBlending;
            } else if (currentType === "cyber_rain") {
              pMaterial.color.setHex(0x22d3ee); // シアンブルーの縦落ちデジタルレイン
              pMaterial.size = 0.022;
              pMaterial.blending = THREE.AdditiveBlending;
            } else {
              pMaterial.color.setHex(0x8b5cf6); // デフォルト：ルキルキ・サイバーパープル
              pMaterial.size = 0.035;
              pMaterial.blending = THREE.AdditiveBlending;
            }

            // 3. 各エフェクトごとの数理物理シミュレーションループ
            for (let i = 0; i < particleCount; i++) {
              if (currentType === "incense_smoke") {
                // 🌀 お香の煙: サイン・コサイン波で螺旋を描きながら、ゆっくりと上空へ立ち上る
                posArr[i * 3] += Math.sin(elapsedTime * 1.5 + i) * 0.0015;
                posArr[i * 3 + 1] += Math.abs(vels[i * 3 + 1]) * delta * 0.35;
                posArr[i * 3 + 2] += Math.cos(elapsedTime * 1.5 + i) * 0.0015;

                // 天井に達したら足元からリスポーン
                if (posArr[i * 3 + 1] > 0.6) {
                  posArr[i * 3] = (Math.random() - 0.5) * 0.15;
                  posArr[i * 3 + 1] = -0.2;
                  posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.15;
                }
              } 
              else if (currentType === "cherry_blossom") {
                // 🌸 桜吹雪: ひらひらと舞い落ちつつ、一方向の風（X軸正方向）に優雅に流される
                posArr[i * 3] += 0.04 * delta; // 風のブレンド
                posArr[i * 3 + 1] -= Math.abs(vels[i * 3 + 1]) * delta * 0.25; // 落下
                posArr[i * 3 + 2] += Math.sin(elapsedTime * 2.0 + i) * 0.001;

                if (posArr[i * 3 + 1] < -0.3 || posArr[i * 3] > 0.4) {
                  posArr[i * 3] = (Math.random() - 0.6) * 0.5; // 左寄りの頭上から再リスポーン
                  posArr[i * 3 + 1] = 0.6;
                  posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.4;
                }
              } 
              else if (currentType === "snow_crystal") {
                // ❄️ 結晶: 重力に抗うように左右にしんしんと揺れながら直下する
                posArr[i * 3] += Math.sin(elapsedTime * 0.8 + i) * 0.0008;
                posArr[i * 3 + 1] -= 0.06 * delta; 
                posArr[i * 3 + 2] += Math.cos(elapsedTime * 0.5 + i) * 0.0005;

                if (posArr[i * 3 + 1] < -0.3) {
                  posArr[i * 3] = (Math.random() - 0.5) * 0.5;
                  posArr[i * 3 + 1] = 0.6;
                  posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.5;
                }
              }
              else if (currentType === "cyber_rain") {
                // 🌧️ デジタルレイン: 高速で上空からマトリックスのように突き抜ける
                posArr[i * 3 + 1] -= 0.8 * delta; // 超高速垂直落下

                if (posArr[i * 3 + 1] < -0.3) {
                  posArr[i * 3] = (Math.random() - 0.5) * 0.5;
                  posArr[i * 3 + 1] = 0.6;
                  posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.5;
                }
              }
              else {
                // ⚡ デフォルト（cyber_glow）: 従来の下から上へ勢いよく吹き出して重力減速するサイバー噴出
                posArr[i * 3] += vels[i * 3] * delta;
                posArr[i * 3 + 1] += vels[i * 3 + 1] * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
                vels[i * 3 + 1] -= delta * 0.2; // 簡易重力減速
              }
            }

            particlesRef.current.geometry.attributes.position.needsUpdate = true;

            // 通常のサイバーグロウ（単発バースト）のみ徐々に消灯させ、環境常駐系は高不透明度を維持
            if (currentType === "cyber_glow") {
              if (pMaterial.opacity > 0) pMaterial.opacity -= delta * 1.4;
            } else {
              pMaterial.opacity = 0.85; // 桜や煙は空間に美しく定着させる
            }
          }

          // リップシンクアンプリチュードの反映（既存流用）
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
    return () => { if (localRenderer) { try { localRenderer.setAnimationLoop(null); } catch(e){} } if (mindarThreeInstance) { try { mindarThreeInstance.stop(); } catch(e){} } };
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
    const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d"); if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  };

  const toggleListening = () => {
    if (!recognitionRef.current) return;
    if (isListening) { recognitionRef.current.stop(); } 
    else {
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

    const inputEl = e.currentTarget.querySelector('input[name="message"]') as HTMLInputElement;
    if (inputEl) inputEl.value = "";
    setSubtitle("（ルキルキが思考を同期中...）");
    setAiStatus("thinking");

    const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    setChatHistory(prev => [...prev, { role: "user", text, timestamp: timeStampStr }]);

    try {
      const gps = await getGPSLocation();
      const imageBase64 = captureARCameraFrame();

      const payload = {
        message: text,
        wallet_address: address || null,
        image_base64: imageBase64,
        latitude: gps?.lat || null,
        longitude: gps?.lng || null,
        history: chatHistory.map(h => ({ role: h.role, text: h.text }))
      };

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const resData = await res.json();

      if (resData.status === "success") {
        setSubtitle(resData.reply);
        setChatHistory(prev => [...prev, { role: "ruki", text: resData.reply, timestamp: timeStampStr }]);

        // 💡 APIのHTTPレスポンスから返ってきたエフェクトIDも即時反映
        if (resData.spatial_effect) {
          setSpatialEffect(resData.spatial_effect);
          effectRef.current = resData.spatial_effect;
          isEffectChangedRef.current = true;
        }

        if (resData.audio_data && audioInstanceRef.current) {
          const binaryString = window.atob(resData.audio_data);
          const bytes = new Uint8Array(binaryString.length);
          for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
          const audioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
          audioInstanceRef.current.onended = () => { setAiStatus("idle"); URL.revokeObjectURL(audioUrl); };
          initAudioPipeline(audioInstanceRef.current);
          audioInstanceRef.current.src = audioUrl;
          setAiStatus("talking");
          await audioInstanceRef.current.play();
        } else {
          setAiStatus("talking"); setTimeout(() => setAiStatus("idle"), 4000);
        }
      } else {
        setSubtitle("通信ノイズが発生しました。"); setAiStatus("idle");
      }
    } catch (err) {
      console.error(err); setSubtitle("エラーが発生しました。"); setAiStatus("idle");
    }
  };

  return (
    <div className="relative w-screen h-screen bg-black overflow-hidden select-none font-mono tracking-tight text-white">
      <div ref={containerRef} className="w-full h-full" />

      {/* 🔮 空間文脈オーバーレイインジケーター */}
      <div className="absolute top-4 left-4 z-50 flex flex-col gap-1 bg-black/60 backdrop-blur-md border border-cyan-500/30 p-3 rounded-lg text-[10px] text-cyan-400">
        <div className="flex items-center gap-1.5 font-bold">
          <span className="w-2 h-2 bg-cyan-400 rounded-full animate-ping" />
          <span>XR OBSERVER LINK: ONLINE</span>
        </div>
        <div>EFFECT_STATE: <span className="text-purple-400 font-bold uppercase">{spatialEffect}</span></div>
        <div className="text-gray-400 text-[9px] mt-1">SYS_TIME: {currentDateTime}</div>
      </div>

      {/* 字幕レイヤー */}
      <div className="absolute bottom-28 left-4 right-4 z-40 bg-black/75 border border-purple-500/20 rounded-xl p-4 min-h-[70px] backdrop-blur-md shadow-2xl transition-all duration-300">
        <div className="text-[10px] text-purple-400 font-bold tracking-widest mb-1.5 flex items-center justify-between">
          <span>RUKI_RUKI v5</span>
          <span className="bg-purple-950/50 px-1.5 py-0.5 rounded border border-purple-500/30 font-normal uppercase text-[8px]">
            {aiStatus}
          </span>
        </div>
        <p className="text-xs text-purple-100 font-medium leading-relaxed">{subtitle}</p>
      </div>

      {/* 統合コントロールコンソール */}
      <div className="absolute bottom-6 left-4 right-4 z-50 flex items-center gap-2.5">
        <form onSubmit={handleSendMessage} className="flex-1 flex bg-black/80 border border-cyan-500/30 rounded-xl p-1.5 backdrop-blur-md shadow-[0_0_20px_rgba(6,182,212,0.15)] focus-within:border-cyan-400 transition-all">
          <input
            ref={inputRef}
            name="message"
            type="text"
            placeholder="教授、次の観測対象の指示を..."
            className="flex-1 bg-transparent border-none outline-none text-xs px-3 text-cyan-100 placeholder-cyan-700/60"
          />
          <button type="submit" className="bg-cyan-950/50 hover:bg-cyan-900 border border-cyan-500/40 text-cyan-400 rounded-lg text-xs px-4 py-2 font-bold transition-all active:scale-95">
            SYNC
          </button>
        </form>

        <button
          onClick={toggleListening}
          className={`p-3.5 rounded-xl border flex items-center justify-center shadow-xl transition-all active:scale-90 ${
            isListening
              ? "bg-red-950/60 border-red-500 text-red-400 animate-pulse"
              : "bg-purple-950/40 border-purple-500/40 text-purple-400 hover:bg-purple-900/50"
          }`}
        >
          🎤
        </button>
      </div>
    </div>
  );
}