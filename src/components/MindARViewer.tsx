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
  const [engraveToast, setEngraveToast] = useState<string>(""); // Arweave刻印完了トースト
  const [spotProposal, setSpotProposal] = useState<string>(""); // 場所登録提案中のスポット名
  const [showImageUrl, setShowImageUrl] = useState<string>(""); // SHOW_IMAGEタグで表示する画像URL
  const [isUploadingMemory, setIsUploadingMemory] = useState<boolean>(false);

  // ── 平面配置モード ──
  const [arPhase, setArPhase] = useState<"mindar" | "placing" | "placed">("mindar");
  const arPhaseRef = useRef<"mindar" | "placing" | "placed">("mindar");

  // Three.jsモジュール参照
  const threeRef = useRef<any>(null);

  // 平面モード Three.js 独立レンダラー用
  const planeRendererRef = useRef<any>(null);
  const planeSceneRef = useRef<any>(null);
  const planeCameraRef = useRef<any>(null);
  const planeRootRef = useRef<any>(null);
  const planeVideoRef = useRef<HTMLVideoElement | null>(null);
  const planeAnimLoopRef = useRef<number | null>(null);

  // デバイスオリエンテーション（平面モード用）
  const orientRef = useRef({ alpha: 0, beta: 0, gamma: 0, hasOri: false, screenAngle: 0 });

  const lastGreetingTimeRef = useRef<number>(0);
  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { address } = useAccount();
  const timersRef = useRef<NodeJS.Timeout[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

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

  const addressRef = useRef(address);
  useEffect(() => { addressRef.current = address; }, [address]);

  // 日時更新
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

  // オーディオ初期化
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

  // アニメーション制御
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

  // 音声認識
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

  // WebSocket
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
          if (data.spatial_effect) {
            setSpatialEffect(data.spatial_effect);
            currentEffectRef.current = data.spatial_effect;
          }

          if (data.type === "proactive_speech") {
            if (audioInstanceRef.current) {
              audioInstanceRef.current.pause();
              audioInstanceRef.current.src = "";
            }
            timersRef.current.forEach(clearTimeout);
            timersRef.current = [];

            setSubtitle(data.reply);

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
                setAiStatus("talking");
                setTimeout(() => setAiStatus("idle"), 5000);
              }
            } else {
              setAiStatus("talking");
              setTimeout(() => setAiStatus("idle"), 5000);
            }
          }
        } catch (err) {
          console.log("WSメッセージパース失敗:", err);
        }
      };

      socket.onclose = () => {
        wsRef.current = null;
        reconnectTimeout = setTimeout(() => {
          if (!wsRef.current) connectWebSocket();
        }, 5000);
      };
    };

    connectWebSocket();
    return () => {
      if (socket) socket.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []);

  // MindAR & Three.js 初期化
  useEffect(() => {
    let mindarThreeInstance: any = null;
    let localRenderer: any = null; 

    const start = async () => {
      try {
        const THREE = await import("three");
        threeRef.current = THREE;
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
        renderer.setClearColor(0x000000, 0);

        const ambientLight = new THREE.AmbientLight(0xffffff, 1.2); 
        scene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.6); 
        directionalLight.position.set(0, 2, 10); 
        scene.add(directionalLight);

        const anchor = mindarThree.addAnchor(0);

        const particleCount = 120; 
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
          gltf.scene.rotation.x = Math.PI / 2; // MindARアンカー用の初期傾き
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
          if (arPhaseRef.current !== "mindar") return;

          console.log("[ARフェーズ] カード認識成功 → 平面配置モードへ移行");
          renderer.setAnimationLoop(null);

          if (containerRef.current) {
            Array.from(containerRef.current.querySelectorAll("video, canvas")).forEach((el) => {
              (el as HTMLElement).style.visibility = "hidden";
            });
          }

          const mindVideo = containerRef.current?.querySelector("video") as HTMLVideoElement | null;
          const existingStream = mindVideo?.srcObject as MediaStream | null;

          const setupPlaneMode = (stream: MediaStream) => {
            const vid = document.createElement("video");
            vid.srcObject = stream;
            vid.autoplay = true;
            vid.playsInline = true;
            vid.muted = true;
            vid.setAttribute("playsinline", "");
            vid.style.cssText = "position:fixed;top:0;left:0;width:100vw;height:100vh;object-fit:cover;z-index:1;";
            document.body.appendChild(vid);
            vid.play().catch(() => {});
            planeVideoRef.current = vid;

            let _alpha = 0, _beta = 0, _gamma = 0, _orient = 0, _hasOri = false;
            const _zee   = new THREE.Vector3(0, 0, 1);
            const _euler = new THREE.Euler();
            const _q0    = new THREE.Quaternion();
            const _q1    = new THREE.Quaternion(-Math.sqrt(0.5), 0, 0, Math.sqrt(0.5));

            const onOri = (e: DeviceOrientationEvent) => {
              if (e.alpha == null || e.beta == null || e.gamma == null) return;
              _hasOri = true;
              _alpha = THREE.MathUtils.degToRad(e.alpha);
              _beta  = THREE.MathUtils.degToRad(e.beta);
              _gamma = THREE.MathUtils.degToRad(e.gamma);
              orientRef.current = { alpha: _alpha, beta: _beta, gamma: _gamma, hasOri: true, screenAngle: _orient };
            };
            const onScreenOri = () => {
              _orient = THREE.MathUtils.degToRad(
                window.screen.orientation?.angle ?? (window as any).orientation ?? 0
              );
            };
            window.addEventListener("orientationchange", onScreenOri);
            onScreenOri();

            const DOE = DeviceOrientationEvent as any;
            if (typeof DOE.requestPermission === "function") {
              DOE.requestPermission()
                .then((s: string) => {
                  if (s === "granted") window.addEventListener("deviceorientation", onOri);
                })
                .catch(() => window.addEventListener("deviceorientation", onOri));
            } else {
              window.addEventListener("deviceorientation", onOri);
            }

            renderer.setClearColor(0x000000, 0);
            renderer.setSize(window.innerWidth, window.innerHeight);
            const rendererCanvas = renderer.domElement;
            rendererCanvas.style.cssText = "position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:2;pointer-events:none;";
            document.body.appendChild(rendererCanvas);
            planeRendererRef.current = renderer;

            const planeScene = new THREE.Scene();
            planeSceneRef.current = planeScene;

            const planeCamera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.01, 100);
            planeCamera.position.set(0, 1.6, 0);
            planeCameraRef.current = planeCamera;

            planeScene.add(new THREE.AmbientLight(0xffffff, 0.8));
            const dl = new THREE.DirectionalLight(0xffffff, 0.6);
            dl.position.set(1, 3, 2);
            planeScene.add(dl);

            const planeRoot = new THREE.Group();
            planeRoot.visible = false;
            planeScene.add(planeRoot);
            planeRootRef.current = planeRoot;

            // ⭕【地面定着化のための修正箇所】
            if (avatarSceneRef.current) {
              const av = avatarSceneRef.current;
              if (av.parent) av.parent.remove(av);
              
              av.position.set(0, 0, 0);
              av.scale.set(1, 1, 1);
              
              // viewer_plane.html の直立用回転ピボット軸(-Math.PI / 2)を正確に反映
              av.rotation.set(-Math.PI / 2, 0, 0); 
              planeRoot.add(av);
            }

            const applyDeviceOrientation = () => {
              if (!_hasOri) return;
              _euler.set(_beta, _alpha, -_gamma, "YXZ");
              planeCamera.quaternion.setFromEuler(_euler);
              planeCamera.quaternion.multiply(_q1);
              planeCamera.quaternion.multiply(_q0.setFromAxisAngle(_zee, -_orient));
            };

            const planeClock = new Clock();
            let pBlinkTimer = 0, pIsBlinking = false, pBlinkDuration = 0.14, pNextBlinkTime = 2.0 + Math.random() * 4.0;

            const planeLoop = () => {
              planeAnimLoopRef.current = requestAnimationFrame(planeLoop);
              applyDeviceOrientation();

              const delta = planeClock.getDelta();
              if (mixerRef.current) mixerRef.current.update(delta);

              // リップシンク同期
              if (analyserRef.current && freqDataRef.current && mouthTargetsRef.current.length > 0) {
                analyserRef.current.getByteFrequencyData(freqDataRef.current);
                let sum = 0;
                for (let i = 0; i < freqDataRef.current.length; i++) sum += freqDataRef.current[i];
                const avg = sum / freqDataRef.current.length;
                const mouthOpen = Math.min(avg / 42, 1.0);

                mouthTargetsRef.current.forEach((t) => {
                  t.idxs.forEach((idx) => {
                    t.mesh.morphTargetInfluences[idx] = mouthOpen;
                  });
                });
              }

              // 自動瞬き同期
              pBlinkTimer += delta;
              if (pIsBlinking) {
                if (pBlinkTimer >= pBlinkDuration) {
                  pIsBlinking = false;
                  pBlinkTimer = 0;
                  pNextBlinkTime = 2.0 + Math.random() * 4.0;
                  blinkTargetsRef.current.forEach((t) => {
                    t.idxs.forEach((idx) => { t.mesh.morphTargetInfluences[idx] = 0; });
                  });
                } else {
                  const inf = Math.sin((pBlinkTimer / pBlinkDuration) * Math.PI);
                  blinkTargetsRef.current.forEach((t) => {
                    t.idxs.forEach((idx) => { t.mesh.morphTargetInfluences[idx] = inf; });
                  });
                }
              } else if (pBlinkTimer >= pNextBlinkTime) {
                pIsBlinking = true;
                pBlinkTimer = 0;
              }

              renderer.render(planeScene, planeCamera);
            };

            planeAnimLoopRef.current = requestAnimationFrame(planeLoop);
            setIsTargetFound(true);
            arPhaseRef.current = "placing";
            setArPhase("placing");
            setSubtitle("ターゲット認識完了。画面をタップしてルキルキを地面に降ろしてください。");
          };

          if (existingStream) {
            setupPlaneMode(existingStream);
          } else {
            navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
              .then(setupPlaneMode)
              .catch((e) => console.error("[平面モード] カメラ取得失敗:", e));
          }
        };

        anchor.onTargetLost = () => {
          if (arPhaseRef.current !== "mindar") return;
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
          if (mixerRef.current) mixerRef.current.update(delta);

          if (isSpawningRef.current && avatarSceneRef.current) {
            if (spawnProgressRef.current < 1.0) {
              spawnProgressRef.current += delta * 0.45;
              const s = Math.min(spawnProgressRef.current, 1.0) * 0.22;
              avatarSceneRef.current.scale.set(s, s, s);
            }
          }

          if (particlesRef.current && particleVelocitiesRef.current) {
            const posArr = particlesRef.current.geometry.attributes.position.array as Float32Array;
            const vels = particleVelocitiesRef.current;
            const currentEffect = currentEffectRef.current;

            for (let i = 0; i < posArr.length / 3; i++) {
              if (currentEffect === "sakura") {
                posArr[i * 3] += vels[i * 3] * delta * 0.15;
                posArr[i * 3 + 1] -= 0.08 * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.15;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (currentEffect === "snow") {
                posArr[i * 3] += vels[i * 3] * delta * 0.1;
                posArr[i * 3 + 1] -= 0.12 * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.1;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (currentEffect === "rain") {
                posArr[i * 3 + 1] -= 1.9 * delta;
                if (posArr[i * 3 + 1] < -0.4) {
                  posArr[i * 3 + 1] = 0.4;
                  posArr[i * 3] = (Math.random() - 0.5) * 0.3;
                }
              } else {
                posArr[i * 3] += vels[i * 3] * delta;
                posArr[i * 3 + 1] += vels[i * 3 + 1] * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
                vels[i * 3 + 1] -= delta * 0.2;
              }
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
          }

          // マウス(リップシンク)
          if (analyserRef.current && freqDataRef.current && mouthTargetsRef.current.length > 0) {
            analyserRef.current.getByteFrequencyData(freqDataRef.current);
            let sum = 0;
            for (let i = 0; i < freqDataRef.current.length; i++) sum += freqDataRef.current[i];
            const avg = sum / freqDataRef.current.length;
            const mouthOpen = Math.min(avg / 42, 1.0);

            mouthTargetsRef.current.forEach((t) => {
              t.idxs.forEach((idx) => { t.mesh.morphTargetInfluences[idx] = mouthOpen; });
            });
          }

          // 瞬き
          blinkTimer += delta;
          if (isBlinking) {
            if (blinkTimer >= blinkDuration) {
              isBlinking = false;
              blinkTimer = 0;
              nextBlinkTime = 2.0 + Math.random() * 4.0;
              blinkTargetsRef.current.forEach((t) => {
                t.idxs.forEach((idx) => { t.mesh.morphTargetInfluences[idx] = 0; });
              });
            } else {
              const inf = Math.sin((blinkTimer / blinkDuration) * Math.PI);
              blinkTargetsRef.current.forEach((t) => {
                t.idxs.forEach((idx) => { t.mesh.morphTargetInfluences[idx] = inf; });
              });
            }
          } else if (blinkTimer >= nextBlinkTime) {
            isBlinking = true;
            blinkTimer = 0;
          }

          renderer.render(scene, camera);
        });

      } catch (err) {
        console.error("XRシステム初期化に致命的なエラーが発生しました:", err);
        setSubtitle("XRの初期化に失敗しました。カメラ権限を確認してください。");
      }
    };

    start();

    return () => {
      if (mindarThreeInstance) mindarThreeInstance.stop();
      if (localRenderer) localRenderer.setAnimationLoop(null);
      if (planeAnimLoopRef.current) cancelAnimationFrame(planeAnimLoopRef.current);
      if (planeVideoRef.current && planeVideoRef.current.parentNode) {
        planeVideoRef.current.parentNode.removeChild(planeVideoRef.current);
      }
    };
  }, []);

  // ⭕【地面への配置ロジックの修正箇所】
  const placeOnGround = () => {
    const root = planeRootRef.current;
    const cam  = planeCameraRef.current;
    if (!root || !cam) return;

    const T = threeRef.current;
    if (!T) return;

    // 前方ベクトル算出
    const dir = new T.Vector3(0, 0, -1);
    dir.applyQuaternion(cam.quaternion);
    dir.y = 0; // 地面と並行にするため垂直の傾きを破棄
    if (dir.lengthSq() < 0.001) dir.set(0, 0, -1);
    dir.normalize();

    const DIST = 2.0; // 2メートル前方に降ろす

    // viewer_plane.html のタップ配置位置計算を再現
    const hit = new T.Vector3(
      cam.position.x + dir.x * DIST,
      0, // 地面（高さ0）に完全接地
      cam.position.z + dir.z * DIST
    );

    root.position.set(hit.x, hit.y, hit.z);
    root.scale.setScalar(2.0); // オブジェクトスケール適正化

    // 親グループの回転をX:0, Z:0の「水平」に完全ロックし、Y軸（旋回）の傾きのみにする
    // これによりデバイスをどのように傾けてタップしても、モデルが斜めにならず地面に直立します
    root.rotation.set(0, 0, 0); 

    root.visible = true;

    arPhaseRef.current = "placed";
    setArPhase("placed");
    setSubtitle("ルキルキを地面に固定しました！");

    if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
    triggerInitialGreeting(null);
  };

  const getGPSLocation = (): Promise<{ lat: number; lng: number } | null> => {
    return new Promise((resolve) => {
      if (!navigator.geolocation) {
        resolve(null);
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (position) => resolve({ lat: position.coords.latitude, lng: position.coords.longitude }),
        () => resolve(null),
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
      );
    });
  };

  const playFixedGreeting = async () => {
    lastGreetingTimeRef.current = Date.now();
    if (audioInstanceRef.current) {
      audioInstanceRef.current.pause();
      audioInstanceRef.current.src = "";
    }
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];

    const fixedGreeting = "こんにちは、まがときさん。ルキルキ、現実空間への同期完了です。";
    setSubtitle(fixedGreeting);
    setAiStatus("talking");
    setSearchPhase("STABLE");
    setSpatialEffect("cyber");
    currentEffectRef.current = "cyber";

    const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    setChatHistory([{ role: "ruki", text: fixedGreeting, timestamp: timeStampStr }]);

    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/voice?text=${encodeURIComponent(fixedGreeting)}`);
      if (response.ok) {
        const blob = await response.blob();
        const audioUrl = URL.createObjectURL(blob);
        if (audioInstanceRef.current) {
          audioInstanceRef.current.onended = () => { 
            setAiStatus("idle"); 
            URL.revokeObjectURL(audioUrl);
          };
          initAudioPipeline(audioInstanceRef.current);
          audioInstanceRef.current.src = audioUrl;
          await audioInstanceRef.current.play();
        }
      }
    } catch {
      setTimeout(() => setAiStatus("idle"), 4000);
    }
  };

  const triggerInitialGreeting = async (imageBase64: string | null) => {
    const timeSinceLast = Date.now() - lastGreetingTimeRef.current;
    if (timeSinceLast < 12000) {
      console.log("初期挨拶スキップ (クールダウン中)");
      return;
    }
    await playFixedGreeting();

    const location = await getGPSLocation();
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: "[INITIAL_GREETING]",
            wallet_address: addressRef.current || null,
            image_base64: imageBase64,
            latitude: location ? location.lat : null,
            longitude: location ? location.lng : null,
            history: []
          }),
        });
        if (!response.ok) throw new Error("API初期挨拶同期エラー");
        const data = await response.json();
        if (data.spatial_effect) {
          setSpatialEffect(data.spatial_effect);
          currentEffectRef.current = data.spatial_effect;
        }
        if (data.spot_proposal) setSpotProposal(data.spot_proposal);
        if (data.arweave_tx_id) {
          setEngraveToast(data.arweave_tx_id);
          setSpatialEffect("sakura");
          currentEffectRef.current = "sakura";
          setTimeout(() => setEngraveToast(""), 8000);
        }
      } catch (e) {
        console.error("初期ログ同期エラー:", e);
      }
    }
  };

  const captureARCameraFrame = (): string | null => {
    if (planeRendererRef.current && planeSceneRef.current && planeCameraRef.current) {
      planeRendererRef.current.render(planeSceneRef.current, planeCameraRef.current);
      const dataUrl = planeRendererRef.current.domElement.toDataURL("image/jpeg", 0.7);
      return dataUrl.split(",")[1] || null;
    }
    return null;
  };

  const handleToggleListening = () => {
    if (!recognitionRef.current) return;
    if (isListening) {
      recognitionRef.current.stop();
    } else {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause();
        audioInstanceRef.current.src = "";
      }
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

    if (audioInstanceRef.current) {
      audioInstanceRef.current.pause();
      audioInstanceRef.current.src = "";
    }
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];

    setSubtitle(`思考中... 「${text}」`);
    setAiStatus("thinking");
    setSearchPhase("CONNECTING...");

    const timeStampStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const updatedHistory = [ ...chatHistory, { role: "user", text, timestamp: timeStampStr } ];
    setChatHistory(updatedHistory);

    timersRef.current.push(
      setTimeout(() => {
        setSearchPhase("TAVILY_SEARCHING...");
        setSubtitle(`🌐 外部情報空間を走査中...\n（Tavilyサーチを同期しています）`);
      }, 1800),
      setTimeout(() => {
        setSearchPhase("DATA_ANALYZING...");
        setSubtitle(`🔮 取得した時間軸データを展開中...\n（ルキルキが回答を再構成しています）`);
      }, 5000)
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
            history: updatedHistory.map(h => ({ role: h.role, content: h.text }))
          })
        });

        timersRef.current.forEach(clearTimeout);
        setSearchPhase("STABLE");

        if (!response.ok) throw new Error("サーバーレスポンス異常");

        const data = await response.json();
        if (data.spatial_effect) {
          setSpatialEffect(data.spatial_effect);
          currentEffectRef.current = data.spatial_effect;
        }
        if (data.spot_proposal) setSpotProposal(data.spot_proposal);
        if (data.arweave_tx_id) {
          setEngraveToast(data.arweave_tx_id);
          setSpatialEffect("sakura");
          currentEffectRef.current = "sakura";
          setTimeout(() => setEngraveToast(""), 8000);
        }

        setSubtitle(data.reply);
        const rukiTimeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        setChatHistory(prev => [...prev, { role: "ruki", text: data.reply, timestamp: rukiTimeStr }]);

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
          } catch {
            setAiStatus("talking");
            setTimeout(() => setAiStatus("idle"), 5000);
          }
        } else {
          setAiStatus("talking");
          setTimeout(() => setAiStatus("idle"), 5000);
        }
      } catch {
        timersRef.current.forEach(clearTimeout);
        setSearchPhase("OFFLINE");
        setSubtitle("バックエンドとの通信に失敗しました。");
        setAiStatus("idle");
      }
    }
  };

  const isControlDisabled = arPhase !== "placed" || aiStatus === "thinking";

  return (
    <>
      {showImageUrl && (
        <div className="fixed inset-0 z-[250] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowImageUrl("")}>
          <div className="relative max-w-[85vw] max-h-[70vh]">
            <img src={showImageUrl} alt="記憶の写真" className="rounded-2xl shadow-[0_0_40px_rgba(168,85,247,0.6)] max-w-full max-h-[70vh] object-contain" />
            <div className="absolute bottom-3 left-0 right-0 text-center text-xs text-purple-200 bg-black/50 py-1 rounded-b-2xl">
              タップで閉じる
            </div>
          </div>
        </div>
      )}

      {isUploadingMemory && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-[200] bg-black/80 border border-purple-400/50 px-4 py-2 rounded-xl text-purple-300 text-xs">
          📷 記憶の写真を刻んでいます... </div>
      )}

      {engraveToast && (
        <div className="fixed top-20 left-1/2 -translate-x-1/2 z-[200] w-[88vw] bg-gradient-to-r from-purple-900/90 to-indigo-900/90 border border-purple-400/60 backdrop-blur-md p-3.5 rounded-2xl shadow-[0_0_25px_rgba(168,85,247,0.4)] text-center animate-bounce">
          <div className="text-purple-300 text-xs font-bold mb-1">✨ 観測データが分散型永続ストレージに刻印されました</div>
          <div className="text-[9px] text-purple-200/70 font-mono break-all bg-black/30 p-1.5 rounded-lg select-all">TX: {engraveToast}</div>
        </div>
      )}

      {arPhase === "mindar" && (
        <div ref={containerRef} className="fixed inset-0 z-0 bg-black" />
      )}

      {arPhase === "placing" && (
        <div className="fixed inset-0 z-40 flex flex-col items-center justify-center bg-black/20" onClick={placeOnGround}>
          <div className="relative w-44 h-44 flex items-center justify-center mb-8 pointer-events-none">
            <div className="absolute inset-0 border-2 border-cyan-400 rounded-full animate-ping opacity-40" />
            <div className="absolute inset-2 border-2 border-cyan-300 rounded-full" />
            <div className="absolute top-1/2 left-0 right-0 h-px bg-cyan-400 -translate-y-1/2" />
            <div className="absolute left-1/2 top-0 bottom-0 w-px bg-cyan-400 -translate-x-1/2" />
          </div>
          <div className="bg-black/70 backdrop-blur-md border border-cyan-400/40 px-6 py-3 rounded-2xl pointer-events-none">
            <p className="text-cyan-300 text-sm font-bold tracking-widest text-center"> タップしてルキルキを地面に配置 </p>
            <p className="text-gray-400 text-xs text-center mt-1"> カメラを地面に向けてタップ </p>
          </div>
        </div>
      )}

      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-mono select-none">
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/60 backdrop-blur-md p-3 rounded-xl text-white border border-purple-500/30 shadow-[0_0_15px_rgba(139,92,246,0.2)]">
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] text-purple-400 font-bold tracking-widest">OBSERVATION SYSTEM v3.5</span>
            <span className="text-xs font-bold text-gray-200">{currentDateTime || "LOADING..."}</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex flex-col items-end gap-0.5">
              <span className="text-[9px] text-gray-400">SYNC_PHASE</span>
              <span className={`text-[10px] font-bold ${searchPhase === "STABLE" ? "text-cyan-400" : "text-yellow-400 animate-pulse"}`}>{searchPhase}</span>
            </div>
            <div className={`w-2.5 h-2.5 rounded-full ${arPhase !== "placed" ? "bg-gray-700" : aiStatus === "thinking" ? "bg-yellow-400 animate-ping" : "bg-purple-500"}`} />
          </div>
        </div>

        <div className="w-full flex flex-col gap-3 items-center">
          <div className="w-full bg-black/75 backdrop-blur-md border border-purple-500/30 p-4 rounded-2xl text-white shadow-[0_4px_25px_rgba(0,0,0,0.6)] flex flex-col gap-2">
            <div className="flex justify-between items-center border-b border-purple-500/20 pb-1.5">
              <span className="text-[10px] text-purple-400 font-bold tracking-widest flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full ${aiStatus === "talking" ? "bg-green-400 animate-pulse" : "bg-purple-500"}`} />
                RUKI_AUDIO_SUBTITLE
              </span>
              {arPhase === "placed" && (
                <button onClick={() => setIsHistoryOpen(true)} className="pointer-events-auto text-[10px] text-cyan-400 hover:text-cyan-300 font-bold border border-cyan-500/30 px-2 py-0.5 rounded-md bg-cyan-950/20 transition-all">
                  LOG_DATA [{chatHistory.length}]
                </button>
              )}
            </div>
            <p className="text-sm leading-relaxed font-sans text-gray-100 whitespace-pre-wrap select-text max-h-[14vh] overflow-y-auto scrollbar-thin">
              {subtitle}
            </p>
          </div>

          <form onSubmit={handleSendMessage} className="w-full flex gap-2 pointer-events-auto items-center">
            <button type="button" onClick={handleToggleListening} disabled={isControlDisabled} className={`p-3.5 rounded-xl border flex items-center justify-center transition-all ${isListening ? "bg-red-600/30 border-red-500 text-red-200 animate-pulse shadow-[0_0_15px_rgba(239,68,68,0.5)]" : "bg-black/60 border-purple-500/30 text-purple-300 hover:bg-purple-950/40"}`}>
              {isListening ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.42 2.72 6.2 6 6.72V21h2v-3.28c3.28-.52 6-3.3 6-6.72h-1.7z"/></svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-7-3h2c0 2.76 2.24 5 5 5s5-2.24 5-5h2c0 3.44-2.43 6.31-5.64 6.84V21h-2.72v-3.16C7.43 17.31 5 14.44 5 11z"/></svg>
              )}
            </button>

            <input ref={inputRef} name="message" type="text" disabled={isControlDisabled} placeholder={arPhase !== "placed" ? "配置を完了させてください..." : aiStatus === "thinking" ? "ルキルキが思考を展開中..." : "ルキルキへ思考データを送信..."} className="flex-1 bg-black/60 border border-purple-500/30 text-white rounded-xl px-4 py-3.5 text-sm focus:outline-none focus:border-cyan-500/60 transition-all font-sans placeholder-gray-500" />
            
            <button type="submit" disabled={isControlDisabled} className="bg-gradient-to-r from-purple-700 via-indigo-700 to-cyan-700 hover:from-purple-600 hover:to-cyan-600 text-white px-5 py-3.5 rounded-xl font-bold text-sm shadow-md disabled:opacity-30 disabled:pointer-events-none transition-all tracking-wider">
              SEND
            </button>
          </form>
        </div>
      </div>

      {isHistoryOpen && (
        <div className="fixed inset-0 z-[300] bg-black/80 backdrop-blur-md flex justify-end animate-fade-in" onClick={() => setIsHistoryOpen(false)}>
          <div className="w-[85vw] max-w-md h-full bg-gradient-to-b from-gray-950 to-black border-l border-purple-500/20 p-5 flex flex-col justify-between shadow-[0_0_50px_rgba(0,0,0,0.8)]" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-center border-b border-purple-500/20 pb-3 mb-4">
              <div>
                <h3 className="text-sm font-bold text-purple-400 tracking-widest">LOG_DATA_STREAM</h3>
                <p className="text-[9px] text-gray-500 mt-0.5">ルキルキとの同期会話ログ履歴</p>
              </div>
              <button onClick={() => setIsHistoryOpen(false)} className="text-xs text-gray-400 border border-purple-500/20 px-3 py-1.5 rounded-md hover:bg-purple-900/60 transition-colors font-bold">
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
                  <div key={idx} className={`p-3.5 rounded-xl border text-xs leading-relaxed shadow-md ${item.role === "user" ? "bg-cyan-950/20 border-cyan-500/30 ml-12" : "bg-purple-950/20 border-purple-500/30 mr-12"}`}>
                    <div className="flex justify-between items-center mb-1.5 text-[10px] font-bold">
                      <span className={item.role === "user" ? "text-cyan-400" : "text-purple-400"}>
                        {item.role === "user" ? "▶ まがときさん" : "◁ ルキルキ SYSTEM"}
                      </span>
                      <span className="text-gray-500 font-normal">{item.timestamp}</span>
                    </div>
                    <p className="font-sans text-gray-200 whitespace-pre-wrap select-text">{item.text}</p>
                  </div>
                ))
              )}
            </div>
            <div className="border-t border-purple-500/10 pt-3 mt-4 text-[9px] text-center text-gray-600 tracking-wider">
              END OF MEMORY LOG CHANNEL
            </div>
          </div>
        </div>
      )}
    </>
  );
}