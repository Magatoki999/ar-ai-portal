'use client';

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WagmiProvider, createConfig } from 'wagmi';
import { polygon } from 'wagmi/chains';
import { getDefaultConfig, RainbowKitProvider, darkTheme } from '@rainbow-me/rainbowkit';

import '@rainbow-me/rainbowkit/styles.css';

const config = getDefaultConfig({
  appName: 'WebAR AI Portal',
  // 💡 WalletConnect Cloud (https://cloud.reown.com/) で取得したProject IDを入れてください
  // テスト用には適当な文字列でも動く場合がありますが、本番は必須です
  projectId: 'YOUR_WALLETCONNECT_PROJECT_ID', 
  chains: [polygon],
  ssr: true, // Next.js(SSR)対応を有効化
});

const queryClient = new QueryClient();

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <WagmiProvider config={config}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider theme={darkTheme({ accentColor: '#7c3aed' })}>
          {children}
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}