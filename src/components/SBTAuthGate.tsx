'use client';

import { useEffect, useState } from 'react';
import { useAccount, useReadContract } from 'wagmi';
import { ConnectButton } from '@rainbow-me/rainbowkit';
import MindARViewer from './MindARViewer';

// ERC-721 / SBT の最小限のABI（保有数を確認する balanceOf のみ）
const sbtAbi = [
  {
    inputs: [{ name: 'owner', type: 'address' }],
    name: 'balanceOf',
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
    type: 'function',
  },
] as const;

// 💡 あなたがPolygon上にデプロイした（またはする予定の）SBTのコントラクトアドレス
//const SBT_CONTRACT_ADDRESS = '0xYourSbtContractAddressHere';
// ─── 修正後 ───
const SBT_CONTRACT_ADDRESS = '0xA3aD030276298785a423223a63Eb86F71F472093';

export default function SBTAuthGate() {
  const { address, isConnected } = useAccount();
  const [mounted, setMounted] = useState(false);

// Next.jsのハイドレーションエラー（SSRとクライアントの差異）を防ぐ対策
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);
  
  // スマートコントラクトから、接続されたアドレスのSBT保有数を読み込む
  const { data: balance, isLoading, isError } = useReadContract({
    address: SBT_CONTRACT_ADDRESS,
    abi: sbtAbi,
    functionName: 'balanceOf',
    args: address ? [address] : undefined,
    query: {
      enabled: !!address, // ウォレットが接続されている場合のみ実行
    },
  });

  if (!mounted) return null;

  // 1. ウォレットが未接続の場合の画面
  if (!isConnected) {
    return (
      <div className="fixed inset-0 bg-gradient-to-br from-gray-900 to-black flex flex-col items-center justify-center p-6 text-white font-sans text-center z-[100]">
        <div className="max-w-sm space-y-6 bg-white/5 border border-white/10 p-8 rounded-3xl backdrop-blur-lg shadow-2xl">
          <div className="h-16 w-16 bg-purple-600/20 text-purple-400 rounded-2xl flex items-center justify-center text-3xl mx-auto animate-pulse">
            🔮
          </div>
          <div className="space-y-2">
            <h1 className="text-xl font-bold tracking-tight">AI人格召喚システム</h1>
            <p className="text-sm text-gray-400 leading-relaxed">
              現実空間へAI人格を召喚するには、認証SBTを保有したウォレットの接続が必要です。
            </p>
          </div>
          <div className="flex justify-center pt-2">
            <ConnectButton label="ウォレットを接続して認証" />
          </div>
        </div>
      </div>
    );
  }

  // 2. コントラクトからのデータ読み込み中の画面
  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-gray-950 flex flex-col items-center justify-center text-white z-[100]">
        <div className="h-8 w-8 border-4 border-purple-500 border-t-transparent rounded-full animate-spin mb-4" />
        <p className="text-sm text-gray-400 font-medium">SBT召喚権限を検証中...</p>
      </div>
    );
  }

  // 3. SBTを保有していない（balanceが0、またはエラー）場合の画面
  const hasSBT = balance && Number(balance) > 0;
  if (!hasSBT || isError) {
    return (
      <div className="fixed inset-0 bg-gradient-to-br from-gray-900 to-black flex flex-col items-center justify-center p-6 text-white font-sans text-center z-[100]">
        <div className="max-w-sm space-y-6 bg-red-950/20 border border-red-500/20 p-8 rounded-3xl backdrop-blur-lg shadow-2xl">
          <div className="h-16 w-16 bg-red-600/20 text-red-400 rounded-2xl flex items-center justify-center text-3xl mx-auto">
            🚫
          </div>
          <div className="space-y-2">
            <h1 className="text-xl font-bold text-red-400">召喚権限がありません</h1>
            <p className="text-sm text-gray-400 leading-relaxed">
              接続されたウォレット（{address?.slice(0, 6)}...{address?.slice(-4)}）に、必要なSBT/NFTが確認できませんでした。
            </p>
          </div>
          <div className="flex justify-center pt-2 gap-3">
            <ConnectButton />
          </div>
        </div>
      </div>
    );
  }

  // 🎉 4. すべての認証を通過した場合、満を持してARビューアを起動！
  return <MindARViewer />;
}