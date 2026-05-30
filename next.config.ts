import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 💡 Turbopackの設定（必要に応じて残す）
  turbopack: {},

  webpack: (config, { isServer }) => {
    // 既存のfs設定
    config.resolve.fallback = {
      ...config.resolve.fallback,
      fs: false,
    };

    if (!isServer) {
      config.externals.push("pino-pretty", "lokijs", "encoding");
    }

    return config;
  },
};

export default nextConfig;