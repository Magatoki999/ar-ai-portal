"use client";

import { useEffect, useRef } from "react";

import { Canvas } from "@react-three/fiber";

import {
  OrbitControls,
  Environment,
  useGLTF,
  useAnimations,
} from "@react-three/drei";

import { Group } from "three";

import { DRACOLoader } from "three-stdlib";

const dracoLoader = new DRACOLoader();

dracoLoader.setDecoderPath(
  "https://www.gstatic.com/draco/versioned/decoders/1.5.7/"
);

function Avatar() {
  const group = useRef<Group>(null);

  const gltf = useGLTF(
    "/avatar.glb",
    true,
    true,
    (loader) => {
      loader.setDRACOLoader(dracoLoader);
    }
  );

  const { actions } = useAnimations(
    gltf.animations,
    group
  );

  useEffect(() => {
    if (actions) {
      const firstAction = Object.values(actions)[0];

      firstAction?.play();
    }
  }, [actions]);

  return (
    <group ref={group}>
      <primitive
        object={gltf.scene}
        scale={1.5}
        position={[0, -1, 0]}
      />
    </group>
  );
}

export default function Home() {
  return (
    <main className="w-screen h-screen">
      <Canvas camera={{ position: [0, 1, 4], fov: 50 }}>
        <ambientLight intensity={1.5} />

        <Environment preset="sunset" />

        <Avatar />

        <OrbitControls />
      </Canvas>
    </main>
  );
}