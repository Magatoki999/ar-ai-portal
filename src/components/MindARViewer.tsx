"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "wagmi";
import type { AnimationMixer, AnimationAction } from "three";

// AIのステート定義
type AIStatus = "idle" | "thinking" | "talking";

export default function MindARViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  
  // ReactでAIの状態を管理
  const [aiStatus, setAiStatus] = useState<AIStatus>("idle");
  const [subtitle, setSubtitle] = useState<string>("（カメラをターゲットにかざしてください）");

  // 💡 接続中のウォレットアドレスを取得（SBTAuthGateを通過しているため確実に取得可能）
  const { address } = useAccount();

  // Three.jsのアニメーション制御用Ref
  const mixerRef = useRef<AnimationMixer | null>(null);
  const actionsRef = useRef<{ [key in AIStatus]?: AnimationAction }>({});
  const activeActionRef = useRef<AnimationAction | null>(null);

  // 1. AIのステート(aiStatus)が変更されたら、3Dモデルのモーションをスムーズに切り替える
  useEffect(() => {
    const fadeToAction = (status: AIStatus, duration: number = 0.5) => {
      const nextAction = actionsRef.current[status];
      const currentAction = activeActionRef.current;

      if (!nextAction || nextAction === currentAction) return;

      // 新しいモーションをフェードイン
      nextAction.reset();
      nextAction.setEffectiveTimeScale(1);
      nextAction.setEffectiveWeight(1);
      nextAction.fadeIn(duration);
      nextAction.play();

      // 前のモーションをフェードアウト
      if (currentAction) {
        currentAction.fadeOut(duration);
      }

      // 現在のアクションを更新
      activeActionRef.current = nextAction;
    };

    fadeToAction(aiStatus);
  }, [aiStatus]);

  // 2. MindAR / Three.js の初期化
  useEffect(() => {
    let mindarThreeInstance: any = null;

    const start = async () => {
      const THREE = await import("three");
      const { MindARThree } = await import("mind-ar/dist/mindar-image-three.prod.js");
      const { GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js");

      const { AnimationMixer: ThreeAnimationMixer, Clock } = THREE;

      const mindarThree = new MindARThree({
        container: containerRef.current!,
        imageTargetSrc: "/targets.mind",
      });
      mindarThreeInstance = mindarThree;

      const { renderer, scene, camera } = mindarThree;

      // ライト配置
      const light = new THREE.HemisphereLight(0xffffff, 0xbbbbff, 1);
      scene.add(light);

      const anchor = mindarThree.addAnchor(0);
      const loader = new GLTFLoader();

      loader.load("/nondraco.glb", (gltf) => {
        gltf.scene.scale.set(0.3, 0.3, 0.3);
        anchor.group.add(gltf.scene);

        if (gltf.animations.length > 0) {
          const mixer = new ThreeAnimationMixer(gltf.scene);
          mixerRef.current = mixer;

          // GLB内のアニメーションを各ステートに割り当て
          actionsRef.current["idle"] = mixer.clipAction(gltf.animations[0]);
          actionsRef.current["talking"] = mixer.clipAction(gltf.animations[1] || gltf.animations[0]);
          actionsRef.current["thinking"] = mixer.clipAction(gltf.animations[2] || gltf.animations[0]);

          // 初期モーション(Idle)の再生
          activeActionRef.current = actionsRef.current["idle"];
          activeActionRef.current.play();
        }
      });

      // ターゲットを認識した時のイベントリスナー
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

    // クリーンアップ
    return () => {
      if (mindarThreeInstance) {
        mindarThreeInstance.stop();
      }
    };
  }, []);

  // 💡 環境変数に応じた通信・フォールバックハンドラー
  const handleSendMessage = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const text = formData.get("message") as string;
    if (!text.trim()) return;

    e.currentTarget.reset();

    // 1. 思考中ステートに変更
    setSubtitle("思考中...");
    setAiStatus("thinking");

    // 環境変数からバックエンドURLを取得
    const baseUrl = process.env.NEXT_PUBLIC_API_URL;

    // 💡 Aパターン: バックエンドのURLがある場合（ローカル開発など）
    if (baseUrl) {
      try {
        const response = await fetch(`${baseUrl}/api/chat`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: text,
            wallet_address: address || null, // 認証済みウォレットアドレスを含める
          }),
        });

        if (!response.ok) {
          throw new Error("APIへの接続に失敗しました");
        }

        const data = await response.json();

        // 発話ステートに変更し、FastAPIから返ってきたレスポンスを表示
        setSubtitle(data.reply);
        setAiStatus("talking");

        // 5秒後に自動でIdle（待機状態）に戻す
        setTimeout(() => {
          setSubtitle("次の指示を待っています。");
          setAiStatus("idle");
        }, 5000);
        return;

      } catch (error) {
        console.error("通信エラー:", error);
        setSubtitle("バックエンドとの通信に失敗しました。");
        setAiStatus("idle");
        return;
      }
    }

    // 💡 Bパターン: バックエンドURLが無い場合（Vercel初期本番環境用セーフティネット）
    setTimeout(() => {
      setSubtitle(`【本番フロントテスト】「${text}」を受信。バックエンド未接続のため、フロント側で召喚を維持します。`);
      setAiStatus("talking");

      // 5秒後にIdleに戻す
      setTimeout(() => {
        setSubtitle("次の指示を待っています。");
        setAiStatus("idle");
      }, 5000);
    }, 2000);
  };

  return (
    <>
      {/* 全画面対策CSS */}
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

      {/* ARカメラコンテナ */}
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

      {/* UIレイヤー (TailwindCSS) */}
      <div className="fixed inset-0 z-50 flex flex-col justify-between pointer-events-none p-4 font-sans">
        
        {/* 上部：ステータスバー */}
        <div className="w-full flex justify-between items-center pointer-events-auto bg-black/50 backdrop-blur-md p-3 rounded-xl text-white border border-white/10">
          <span className="text-xs font-semibold flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${aiStatus === "thinking" ? "bg-yellow-400 animate-pulse" : aiStatus === "talking" ? "bg-green-400 animate-ping" : "bg-blue-400"}`} />
            STATUS: {aiStatus.toUpperCase()}
          </span>
          <div className="flex gap-2">
            {/* デバッグ用手動切り替えボタン */}
            <button onClick={() => setAiStatus("idle")} className="text-[10px] bg-gray-700 px-2 py-1 rounded">Idle</button>
            <button onClick={() => setAiStatus("thinking")} className="text-[10px] bg-yellow-600 px-2 py-1 rounded">Think</button>
            <button onClick={() => setAiStatus("talking")} className="text-[10px] bg-green-600 px-2 py-1 rounded">Talk</button>
          </div>
        </div>

        {/* 下部：字幕 ＆ 入力フォーム */}
        <div className="w-full space-y-3 pointer-events-auto mb-4">
          
          {/* 字幕コンテナ */}
          <div className="bg-black/70 backdrop-blur-lg p-4 rounded-2xl text-white text-center min-h-[70px] flex items-center justify-center border border-white/10 shadow-xl">
            <p className="text-sm font-medium leading-relaxed transition-all duration-300">
              {subtitle}
            </p>
          </div>

          {/* チャット入力フォーム */}
          <form onSubmit={handleSendMessage} className="flex gap-2">
            <input 
              type="text" 
              name="message"
              placeholder="AI人格にメッセージを送信..." 
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