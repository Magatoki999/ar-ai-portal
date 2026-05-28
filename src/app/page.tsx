"use client";

import { Canvas } from "@react-three/fiber";
import { OrbitControls, Box } from "@react-three/drei";

export default function Home() {
  return (
    <main className="w-screen h-screen">
      <Canvas camera={{ position: [3, 3, 3] }}>
        <ambientLight intensity={1} />

        <Box>
          <meshStandardMaterial color="orange" />
        </Box>

        <OrbitControls />
      </Canvas>
    </main>
  );
}