"use client";

import { useEffect, useRef } from "react";
// 1. TypeScript対策: トップレベルで型だけを安全にインポート
import type { AnimationMixer } from "three";

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const start = async () => {
      const THREE = await import("three");

      const { MindARThree } = await import(
        "mind-ar/dist/mindar-image-three.prod.js"
      );

      const { GLTFLoader } = await import(
        "three/examples/jsm/loaders/GLTFLoader.js"
      );

      // 変数名の衝突を避けるためにエイリアス（ThreeAnimationMixer）を適用
      const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE;

      const mindarThree = new MindARThree({
        container: containerRef.current!,
        imageTargetSrc: "/targets.mind",
      });

      const { renderer, scene, camera } = mindarThree;

      const light = new THREE.HemisphereLight(0xffffff, 0xbbbbff, 1);
      scene.add(light);

      const anchor = mindarThree.addAnchor(0);
      const loader = new GLTFLoader();

      // 2. ESLint対策: any型を排除し、初期値のundefinedを許容する型を定義
      let mixer: AnimationMixer | undefined;

      loader.load("/nondraco.glb", (gltf) => {
        gltf.scene.scale.set(0.3, 0.3, 0.3);
        anchor.group.add(gltf.scene);

        if (gltf.animations.length > 0) {
          mixer = new ThreeAnimationMixer(gltf.scene);
          const action = mixer.clipAction(gltf.animations[0]);
          action.play();
        }
      });

      const clock = new Clock();

      await mindarThree.start();

      renderer.setAnimationLoop(() => {
        const delta = clock.getDelta();
        if (mixer) mixer.update(delta);
        renderer.render(scene, camera);
      });
    };

    start();
  }, []);

  return (
    <>
      {/* 3. 全画面対策: MindARが背後で自動生成するvideo/canvasを強制的に画面いっぱいに広げるCSS */}
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

      {/* 親のレイアウトを無視して画面全体をジャックする設定 */}
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
          zIndex: 999,
          backgroundColor: "#000",
        }}
      />
    </>
  );
}