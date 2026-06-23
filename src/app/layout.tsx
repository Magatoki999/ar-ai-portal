// src/app/layout.tsx のイメージ
import type { Viewport } from 'next';
import { Providers } from './providers';
import './globals.css';

// AR体験中はピンチズームや、入力フィールドフォーカス時の自動ズームが
// 画面のレイアウトを崩す原因になるため、ここで明示的に抑制する。
// maximumScale=1 と userScalable=false がズーム自体を止める設定。
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}