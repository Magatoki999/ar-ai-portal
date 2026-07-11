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

  // 2026-07-10追加：MetaMask接続が不安定な環境向けのパスワード認証回避策（テスト運用限定）
  const [passwordAuthed, setPasswordAuthed] = useState(false);
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [password, setPassword] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [isVerifying, setIsVerifying] = useState(false);

// Next.jsのハイドレーションエラー（SSRとクライアントの差異）を防ぐ対策
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
    // 同じタブ内であれば再読み込みのたびにパスワードを求めないようにする
    if (typeof window !== 'undefined' && sessionStorage.getItem('rukiruki_password_authed') === 'true') {
      setPasswordAuthed(true);
    }
  }, []);

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsVerifying(true);
    setPasswordError('');
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL;
      const res = await fetch(`${baseUrl}/api/auth/password`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const data = await res.json();
      if (data.status === 'ok') {
        sessionStorage.setItem('rukiruki_password_authed', 'true');
        setPasswordAuthed(true);
      } else {
        setPasswordError(data.message ?? 'パスワードが違います');
      }
    } catch (err) {
      console.error('[パスワード認証]', err);
      setPasswordError('通信エラーが発生しました');
    } finally {
      setIsVerifying(false);
    }
  };

  // パスワード入力フォーム（未接続画面・SBT未確認画面の両方から呼び出す共通パーツ）
  const passwordFallback = (
    <div className="pt-4 mt-2 border-t border-white/10">
      {!showPasswordForm ? (
        <button
          onClick={() => setShowPasswordForm(true)}
          className="text-xs text-gray-500 hover:text-gray-300 underline transition-colors"
        >
          ウォレットに接続できない場合（テスト用）
        </button>
      ) : (
        <form onSubmit={handlePasswordSubmit} className="space-y-3">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="パスワード"
            autoComplete="off"
            className="w-full bg-black/40 border border-white/20 rounded-xl px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-400/60 transition-colors"
          />
          {passwordError && (
            <p className="text-xs text-red-400">{passwordError}</p>
          )}
          <button
            type="submit"
            disabled={isVerifying || !password}
            className="w-full bg-purple-600/80 hover:bg-purple-500/80 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl px-4 py-2.5 text-sm text-white transition-colors"
          >
            {isVerifying ? '確認中...' : 'パスワードでログイン'}
          </button>
        </form>
      )}
    </div>
  );
  
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

  // パスワード認証済みなら、ウォレット接続を経由せず直接ARビューアを起動する
  // （2026-07-10追加：MetaMask接続が不安定な環境向けの回避策。テスト運用限定）
  if (passwordAuthed) {
    return <MindARViewer />;
  }

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
          {passwordFallback}
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
          {passwordFallback}
        </div>
      </div>
    );
  }

  // 🎉 4. すべての認証を通過した場合、満を持してARビューアを起動！
  return <MindARViewer />;
}