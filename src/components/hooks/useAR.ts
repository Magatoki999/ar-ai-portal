// hooks/useAR.ts
// ─────────────────────────────────────────────────────────────────────────────
// MindARThree の初期化・アニメーションループ・パーティクルエフェクトを管理する。
// 責務:
//   - MindAR / Three.js / GLB の動的 import と初期化
//   - アンカーの onTargetFound / onTargetLost ハンドラ
//   - パーティクルシステム（sakura / snow / rain / cyber）
//   - まばたきアニメーション
//   - アバタースポーン演出（スケール easeOutCubic）
//   - アニメーションミキサー（idle / thinking / talking クロスフェード）
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useEffect, useRef } from "react";
import type { AnimationMixer, AnimationAction } from "three";
import type { AIStatus, MorphTargetRef } from "../lib/types";

const PARTICLE_COUNT = 120;
const DRACO_DECODER_PATH =
  "https://www.gstatic.com/draco/versioned/decoders/1.5.6/";
const GLB_PATH = "/avatar.glb?v=9";
const MIND_PATH = "/targets.mind";

interface UseAROptions {
  containerRef:      React.MutableRefObject<HTMLDivElement | null>;
  currentEffectRef:  React.MutableRefObject<string>;
  mouthTargetsRef:   React.MutableRefObject<MorphTargetRef[]>;
  blinkTargetsRef:   React.MutableRefObject<MorphTargetRef[]>;
  updateMouthMorph:  () => void;
  onTargetFound:     () => void;
  onTargetLost:      () => void;
  onSubtitleChange:  (text: string) => void;
  onStatusChange:    (status: AIStatus) => void;
}

export function useAR({
  containerRef,
  currentEffectRef,
  mouthTargetsRef,
  blinkTargetsRef,
  updateMouthMorph,
  onTargetFound,
  onTargetLost,
  onSubtitleChange,
  onStatusChange,
}: UseAROptions) {
  const mixerRef        = useRef<AnimationMixer | null>(null);
  const actionsRef      = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);
  const particlesRef    = useRef<any>(null);
  const particleVelocitiesRef = useRef<Float32Array | null>(null);
  const spawnProgressRef = useRef<number>(0);
  const isSpawningRef   = useRef<boolean>(false);
  const avatarSceneRef  = useRef<any>(null);
  const onVisibilityChangeRef = useRef<(() => void) | null>(null);

  // ── AIステータス変化時のアニメーションクロスフェード ──
  const fadeToAction = (status: AIStatus, duration: number = 0.5) => {
    const nextAction    = actionsRef.current[status];
    const currentAction = activeActionRef.current;
    if (!nextAction || nextAction === currentAction) return;
    nextAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(duration).play();
    if (currentAction) currentAction.fadeOut(duration);
    activeActionRef.current = nextAction;
  };

  // ── MindAR / Three.js 初期化 ──
  useEffect(() => {
    let mindarThreeInstance: any = null;
    let localRenderer: any = null;
    let onResize: (() => void) | null = null; // cleanup で参照するため外側で宣言

    const start = async () => {
      try {
        const THREE = await import("three");
        const { MindARThree } = await import(
          "mind-ar/dist/mindar-image-three.prod.js"
        );
        const { GLTFLoader }  = await import(
          "three/examples/jsm/loaders/GLTFLoader.js"
        );
        const { DRACOLoader } = await import(
          "three/examples/jsm/loaders/DRACOLoader.js"
        );
        const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE;

        if (!containerRef.current) throw new Error("DOMコンテナが見つかりません。");

        const mindarThree = new MindARThree({
          container:        containerRef.current,
          imageTargetSrc:   MIND_PATH,
          uiLoading:        "no",
          uiScanning:       "no",
          uiError:          "no",
        });
        mindarThreeInstance = mindarThree;
        const { renderer, scene, camera } = mindarThree;
        localRenderer = renderer;

        // ── レンダラー設定 ──
        // ⚠️ renderer.setSize() は MindAR の内部管理と競合するため呼ばない。
        // MindARThree が start() 後に canvas サイズを自分で設定する。
        renderer.outputColorSpace    = THREE.SRGBColorSpace;
        renderer.toneMapping         = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.0;
        renderer.setClearColor(0x000000, 0);

        // リサイズ対応：MindAR に任せつつカメラの aspect だけ同期する
        onResize = () => {
          const w = window.innerWidth;
          const h = window.innerHeight;
          camera.aspect = w / h;
          camera.updateProjectionMatrix();
          // MindAR が canvas を管理しているため renderer.setSize() は呼ばない
        };
        window.addEventListener("resize", onResize);

        // ── ライト ──
        scene.add(new THREE.AmbientLight(0xffffff, 1.2));
        const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
        dirLight.position.set(0, 2, 10);
        scene.add(dirLight);

        const anchor = mindarThree.addAnchor(0);

        // ── パーティクル ──
        const particleGeometry  = new THREE.BufferGeometry();
        const particlePositions = new Float32Array(PARTICLE_COUNT * 3);
        const particleVelocities = new Float32Array(PARTICLE_COUNT * 3);

        for (let i = 0; i < PARTICLE_COUNT; i++) {
          particlePositions[i * 3]     = (Math.random() - 0.5) * 0.4;
          particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 0.4;
          particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 0.4;
          particleVelocities[i * 3]     = (Math.random() - 0.5) * 0.4;
          particleVelocities[i * 3 + 1] = Math.random() * 0.6 + 0.2;
          particleVelocities[i * 3 + 2] = (Math.random() - 0.5) * 0.4;
        }

        particleGeometry.setAttribute(
          "position",
          new THREE.BufferAttribute(particlePositions, 3)
        );
        const particleMaterial = new THREE.PointsMaterial({
          color:       0x8b5cf6,
          size:        0.028,
          transparent: true,
          opacity:     0,
          blending:    THREE.AdditiveBlending,
        });

        const spawnParticles = new THREE.Points(
          particleGeometry,
          particleMaterial
        );
        anchor.group.add(spawnParticles);
        particlesRef.current          = spawnParticles;
        particleVelocitiesRef.current = particleVelocities;

        // ── GLB ロード ──
        const dracoLoader = new DRACOLoader();
        dracoLoader.setDecoderPath(DRACO_DECODER_PATH);
        const loader = new GLTFLoader();
        loader.setDRACOLoader(dracoLoader);

        const localBlinkTargets: MorphTargetRef[] = [];
        const localMouthTargets: MorphTargetRef[] = [];

        loader.load(GLB_PATH, (gltf: any) => {
          gltf.scene.scale.set(0, 0, 0);
          gltf.scene.rotation.x = Math.PI / 2;
          avatarSceneRef.current = gltf.scene;

          gltf.scene.traverse((child: any) => {
            if (child.isMesh && child.morphTargetDictionary) {
              const bIdxs: number[] = [];
              const mIdxs: number[] = [];
              Object.keys(child.morphTargetDictionary).forEach((key) => {
                const lk = key.toLowerCase();
                if (
                  lk === "blink" || lk === "eyeblink" || lk === "close" ||
                  lk.includes("eye_close") || lk.includes("blink_")
                ) bIdxs.push(child.morphTargetDictionary[key]);
                if (
                  lk === "aa" || lk === "a" || lk === "vowel_a" ||
                  lk.includes("mouth_a")
                ) mIdxs.push(child.morphTargetDictionary[key]);
              });
              if (bIdxs.length > 0)
                localBlinkTargets.push({ mesh: child, idxs: bIdxs });
              if (mIdxs.length > 0)
                localMouthTargets.push({ mesh: child, idxs: mIdxs });
            }
            if (child.isMesh && child.material) {
              const materials = Array.isArray(child.material)
                ? child.material
                : [child.material];
              materials.forEach((mat: any) => {
                const isHair =
                  child.name.toLowerCase().includes("hair") ||
                  (mat.name && mat.name.toLowerCase().includes("hair"));
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
            actionsRef.current["idle"]     = mixer.clipAction(gltf.animations[0]);
            actionsRef.current["talking"]  = mixer.clipAction(gltf.animations[2] ?? gltf.animations[0]);
            actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[1] ?? gltf.animations[0]);
            activeActionRef.current = actionsRef.current["idle"]!;
            activeActionRef.current.play();
          }
        });

        // ── ターゲット認識 ──
        anchor.onTargetFound = () => {
          isSpawningRef.current  = true;
          spawnProgressRef.current = 0;
          // パーティクルをリセット
          if (particlesRef.current) {
            (particlesRef.current.material as any).opacity = 1.0;
            const posArr =
              particlesRef.current.geometry.attributes.position.array as Float32Array;
            for (let i = 0; i < PARTICLE_COUNT; i++) {
              posArr[i * 3]     = (Math.random() - 0.5) * 0.2;
              posArr[i * 3 + 1] = -0.2;
              posArr[i * 3 + 2] = (Math.random() - 0.5) * 0.2;
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;
          }
          onTargetFound();
        };

        anchor.onTargetLost = () => {
          isSpawningRef.current = false;
          if (avatarSceneRef.current)
            avatarSceneRef.current.scale.set(0, 0, 0);
          onTargetLost();
        };

        // ── レンダリングループ ──
        const clock = new Clock();
        let blinkTimer = 0,
          isBlinking = false,
          nextBlinkTime = 2.0 + Math.random() * 4.0;
        const BLINK_DURATION = 0.14;

        await mindarThree.start();

        // MindAR start()後、生成された video / canvas を全画面にフィットさせる（黒帯防止）
        if (containerRef.current) {
          const c = containerRef.current;

          // コンテナ自体を確実に全画面に
          c.style.width    = "100vw";
          c.style.height   = "100vh";
          c.style.overflow = "hidden";
          c.style.position = "fixed";
          c.style.top      = "0";
          c.style.left     = "0";

          // MindAR が生成した video
          const videos = c.querySelectorAll("video");
          videos.forEach((v) => {
            (v as HTMLElement).style.width     = "100%";
            (v as HTMLElement).style.height    = "100%";
            (v as HTMLElement).style.objectFit = "cover";
            (v as HTMLElement).style.position  = "absolute";
            (v as HTMLElement).style.top       = "0";
            (v as HTMLElement).style.left      = "0";
          });

          // MindAR が生成した canvas（Three.js レンダラー）
          const canvases = c.querySelectorAll("canvas");
          canvases.forEach((cv) => {
            (cv as HTMLElement).style.width    = "100%";
            (cv as HTMLElement).style.height   = "100%";
            (cv as HTMLElement).style.position = "absolute";
            (cv as HTMLElement).style.top      = "0";
            (cv as HTMLElement).style.left     = "0";
          });
        }

        // ── タブ非表示→再表示時のカメラストリーム再開処理（2026-07-18追加） ──
        // モバイルブラウザは、タブがバックグラウンドになるとカメラの<video>ストリームを
        // 一時停止することがある（省電力・プライバシー保護のため）。屋外での利用では
        // 画面ロックや他アプリへの切り替えが頻発するため、この状態のまま
        // captureFrame() を呼ぶと videoWidth が読めず image_base64 が null になり、
        // 「この場所を記憶して」等の写真保存が静かに失敗する原因になっていた。
        // 復帰時に一時停止中のvideoがあれば明示的に再生し直す。
        const resumeVideoIfPaused = () => {
          if (document.visibilityState !== "visible") return;
          const c = containerRef.current;
          if (!c) return;
          c.querySelectorAll("video").forEach((v) => {
            const videoEl = v as HTMLVideoElement;
            if (videoEl.paused) {
              videoEl.play().catch((err) => {
                console.log("[useAR] タブ復帰時のvideo再開に失敗:", err);
              });
            }
          });
        };
        document.addEventListener("visibilitychange", resumeVideoIfPaused);
        onVisibilityChangeRef.current = resumeVideoIfPaused;

        renderer.setAnimationLoop(() => {
          const delta       = clock.getDelta();
          const elapsedTime = clock.getElapsedTime();

          // アニメーションミキサー
          if (mixerRef.current) mixerRef.current.update(delta);

          // スポーン演出
          if (isSpawningRef.current && avatarSceneRef.current) {
            if (spawnProgressRef.current < 1.0) {
              spawnProgressRef.current += delta * 1.8;
              const p    = Math.min(spawnProgressRef.current, 1.0);
              const ease = 1 - Math.pow(1 - p, 3);
              avatarSceneRef.current.scale.set(ease, ease, ease);
            } else {
              isSpawningRef.current = false;
            }
          }

          // ホバーアニメーション（スポーン完了後）
          if (
            avatarSceneRef.current &&
            !isSpawningRef.current &&
            spawnProgressRef.current >= 1.0
          ) {
            avatarSceneRef.current.position.y =
              Math.sin(elapsedTime * 1.8) * 0.012;
          }

          // まばたき
          if (blinkTargetsRef.current.length > 0) {
            blinkTimer += delta;
            if (!isBlinking && blinkTimer >= nextBlinkTime) {
              isBlinking = true;
              blinkTimer = 0;
            }
            if (isBlinking) {
              if (blinkTimer < BLINK_DURATION) {
                const w = Math.sin((blinkTimer / BLINK_DURATION) * Math.PI);
                blinkTargetsRef.current.forEach((t) =>
                  t.idxs.forEach((idx) => (t.mesh.morphTargetInfluences[idx] = w))
                );
              } else {
                blinkTargetsRef.current.forEach((t) =>
                  t.idxs.forEach((idx) => (t.mesh.morphTargetInfluences[idx] = 0))
                );
                isBlinking = false;
                blinkTimer = 0;
                nextBlinkTime = 1.5 + Math.random() * 4.5;
              }
            }
          }

          // パーティクル
          if (particlesRef.current && particleVelocitiesRef.current) {
            const posArr = particlesRef.current.geometry.attributes.position
              .array as Float32Array;
            const vels   = particleVelocitiesRef.current;
            const effect = currentEffectRef.current;
            const mat    = particlesRef.current.material as any;

            if      (effect === "sakura") mat.color.setHex(0xfbcfe8);
            else if (effect === "snow")   mat.color.setHex(0xffffff);
            else if (effect === "rain")   mat.color.setHex(0x3b82f6);
            else                          mat.color.setHex(0x8b5cf6);

            for (let i = 0; i < PARTICLE_COUNT; i++) {
              if (effect === "sakura") {
                posArr[i * 3]     += vels[i * 3] * delta * 0.4 + Math.sin(elapsedTime + i) * 0.002;
                posArr[i * 3 + 1] -= Math.abs(vels[i * 3 + 1]) * delta * 0.3;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.4 + Math.cos(elapsedTime + i) * 0.002;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (effect === "snow") {
                posArr[i * 3]     += vels[i * 3] * delta * 0.1;
                posArr[i * 3 + 1] -= 0.12 * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta * 0.1;
                if (posArr[i * 3 + 1] < -0.4) posArr[i * 3 + 1] = 0.4;
              } else if (effect === "rain") {
                posArr[i * 3 + 1] -= 1.9 * delta;
                if (posArr[i * 3 + 1] < -0.4) {
                  posArr[i * 3 + 1] = 0.4;
                  posArr[i * 3]     = (Math.random() - 0.5) * 0.3;
                }
              } else {
                posArr[i * 3]     += vels[i * 3] * delta;
                posArr[i * 3 + 1] += vels[i * 3 + 1] * delta;
                posArr[i * 3 + 2] += vels[i * 3 + 2] * delta;
                vels[i * 3 + 1]   -= delta * 0.2;
              }
            }
            particlesRef.current.geometry.attributes.position.needsUpdate = true;

            if (isSpawningRef.current) {
              if (mat.opacity < 0.9) mat.opacity += delta * 2.0;
            } else {
              if (["sakura", "snow", "rain"].includes(effect)) {
                mat.opacity = 0.75;
              } else if (mat.opacity > 0) {
                mat.opacity -= delta * 1.2;
              }
            }
          }

          // 口パク（useVoice から渡されたコールバック）
          updateMouthMorph();

          renderer.render(scene, camera);
        });
      } catch (err: any) {
        console.log("MindAR起動失敗:", err);
        onSubtitleChange(`システム初期化エラー: ${err?.message ?? String(err)}`);
      }
    };

    start();

    return () => {
      try { localRenderer?.setAnimationLoop(null); } catch (_) {}
      try { mindarThreeInstance?.stop(); }            catch (_) {}
      if (onResize) window.removeEventListener("resize", onResize);
      if (onVisibilityChangeRef.current) {
        document.removeEventListener("visibilitychange", onVisibilityChangeRef.current);
      }
    };
  }, []);

  return { mixerRef, actionsRef, activeActionRef, avatarSceneRef, fadeToAction };
}
