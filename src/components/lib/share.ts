// lib/share.ts
// ─────────────────────────────────────────────────────────────────────────────
// X（旧Twitter）へのシェア機能。
//
// 方針：
//   - X公式APIは使わない（2026年に無料枠が実質廃止され、従量課金制になったため）。
//   - 代わりにブラウザ標準のWeb Share API / Web Intentを使う。APIキー・OAuth・
//     バックエンド変更が一切不要で、ユーザーが最後に必ず投稿ボタンを押す
//     （＝勝手に投稿されない）安全な設計。
//
// 挙動：
//   - 画像URLが渡され、かつnavigator.share（画像添付対応）が使える環境（iOS Safari等）
//     では、OSの共有シートを開く。ユーザーが「X」を選べば画像が添付された状態で渡る。
//   - それ以外（画像が無い、またはnavigator.shareが使えないデスクトップブラウザ等）では、
//     X公式のWeb Intent（投稿画面に文章を事前入力するだけの仕組み）にフォールバックする。
// ─────────────────────────────────────────────────────────────────────────────

interface ShareToXOptions {
  /** 投稿本文（ルキルキの返答など） */
  text: string;
  /** スナップ写真等の画像URL（Supabaseの公開URLを想定・任意） */
  imageUrl?: string;
}

/**
 * 画像URLを取得してFileオブジェクトに変換する。
 * navigator.share({ files: [...] }) にはFileオブジェクトが必要なため。
 */
async function urlToFile(url: string, filename: string): Promise<File | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const blob = await res.blob();
    return new File([blob], filename, { type: blob.type || "image/jpeg" });
  } catch (e) {
    console.warn("[share] 画像の取得に失敗しました:", e);
    return null;
  }
}

/**
 * X公式のWeb Intent（投稿画面に文章を事前入力するだけ）を新しいタブで開く。
 * 画像は添付できないため、imageUrlがある場合はリンクとして本文に含める
 * （Xがリンク先を画像プレビューとして展開することがあるが保証はされない）。
 */
function openTweetIntent(text: string, imageUrl?: string): void {
  const fullText = imageUrl ? `${text}\n${imageUrl}` : text;
  const intentUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(fullText)}`;
  window.open(intentUrl, "_blank", "noopener,noreferrer");
}

/**
 * 会話内容（＋任意でスナップ写真）をXにシェアする。
 * 呼び出し例：
 *   await shareToX({ text: "ルキルキの返答テキスト", imageUrl: snapImageUrl });
 */
export async function shareToX({ text, imageUrl }: ShareToXOptions): Promise<void> {
  // 画像があり、かつファイル共有に対応したnavigator.shareが使える場合（iOS Safari等）
  if (imageUrl && typeof navigator !== "undefined" && navigator.share) {
    const file = await urlToFile(imageUrl, "ruki_snap.jpg");
    if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ text, files: [file] });
        return; // 共有シート経由で完了（Xアプリ選択も含めユーザー操作に委ねる）
      } catch (e) {
        // ユーザーがキャンセルした場合(AbortError)は何もしない。
        // それ以外のエラーはWeb Intentにフォールバック。
        if ((e as Error).name === "AbortError") return;
        console.warn("[share] navigator.shareに失敗、Web Intentにフォールバック:", e);
      }
    }
  }

  // 画像なし、またはnavigator.shareが使えない環境（PCブラウザ等）
  if (!imageUrl && typeof navigator !== "undefined" && navigator.share) {
    try {
      await navigator.share({ text });
      return;
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      // フォールバックへ
    }
  }

  openTweetIntent(text, imageUrl);
}
