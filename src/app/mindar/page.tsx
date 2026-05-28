"use client";

import dynamic from "next/dynamic";

const MindARViewer = dynamic(
  () => import("@/components/MindARViewer"),
  {
    ssr: false,
  }
);

export default function Page() {
  return <MindARViewer />;
}