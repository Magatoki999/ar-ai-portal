// lib/audio.ts
// ─────────────────────────────────────────────────────────────────────────────
// 音声関連ユーティリティ。
// base64 → BlobURL 変換・MIME タイプ解決など純粋関数のみを置く。
// ─────────────────────────────────────────────────────────────────────────────

/**
 * base64 エンコードされた音声データを ObjectURL に変換する。
 * 戻り値は使用後に URL.revokeObjectURL() で解放すること。
 */
export function base64ToAudioUrl(
  base64: string,
  mimeType: string = "audio/mpeg"
): string {
  const binaryString = window.atob(base64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return URL.createObjectURL(new Blob([bytes], { type: mimeType }));
}

/**
 * サーバーから返ってくる audio_mime を安全に解決する。
 * 未指定・不明な場合は "audio/mpeg" にフォールバック。
 */
export function resolveAudioMime(mime?: string | null): string {
  if (!mime) return "audio/mpeg";
  if (mime.startsWith("audio/")) return mime;
  return "audio/mpeg";
}
