"use client";

import { useEffect, useRef } from "react";
// 1. トップレベルで型だけを安全にインポート
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

      const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE; // 変数名の衝突を避けるためエイリアス

      const mindarThree = new MindARThree({
        container: containerRef.current!,
        imageTargetSrc: "/targets.mind",
      });

      const { renderer, scene, camera } =
        mindarThree;

      const light = new THREE.HemisphereLight(
        0xffffff,
        0xbbbbff,
        1
      );

      scene.add(light);

      const anchor = mindarThree.addAnchor(0);

      const loader = new GLTFLoader();

      // 2. any をやめて型を指定（初期値は未定義なので undefined も許容）
      let mixer: AnimationMixer | undefined;

      loader.load("/nondraco.glb", (gltf) => {
        gltf.scene.scale.set(0.3, 0.3, 0.3);

        anchor.group.add(gltf.scene);

        if (gltf.animations.length > 0) {
          // 3. ここでインスタンスを代入
          mixer = new ThreeAnimationMixer(gltf.scene);

          const action = mixer.clipAction(
            gltf.animations[0]
          );

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
    <div
      ref={containerRef}
      style={{
        width: "100vw",
        height: "100vh",
      }}
    />
  );
}