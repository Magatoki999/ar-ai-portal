// components/BookScanModal.tsx
// ─────────────────────────────────────────────────────────────────────────────
// 読書通帳機能：バーコードスキャンモーダル。
//
// 重要な設計方針（既存システムへの影響を避けるため）：
//   - navigator.mediaDevices.getUserMedia() を新規に呼ばない。
//     MindARが既に専有しているカメラと競合させないため。
//   - mindarThree.stop()/start() も呼ばない。
//     ライブラリのstop()後の再開には既知の不安定さの報告があるため、
//     既存のAR描画ループには一切触れない設計にしている。
//   - 代わりに、既存の captureFrame（useChat.ts）と同じ手法
//     （containerEl内の<video>からcanvas.drawImageでフレームを読む）を使い、
//     そのcanvasを @zxing/browser の decodeFromCanvas() に渡して
//     バーコードをデコードする。カメラの専有権は一切移動しない。
//
// 呼び出し側（MindARViewer.tsx）には、AR用のcontainerRef（video/canvasの親要素）
// を渡してもらう想定。isTargetLost（顔アイコン表示中）の時にだけ表示する。
// ─────────────────────────────────────────────────────────────────────────────
"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface BookInfo {
  isbn: string;
  title: string;
  author: string | null;
  publisher: string | null;
  price: number | null;
  cover_url: string | null;
}

interface BookScanModalProps {
  containerRef: React.RefObject<HTMLDivElement>;
  onClose: () => void;
  onLogged: (book: BookInfo) => void; // 記帳成功時に呼ばれる（ルキルキへの一言通知等に使う）
}

type ScanState = "scanning" | "looking_up" | "confirm" | "saving" | "not_found" | "error";

const SCAN_INTERVAL_MS = 250; // バーコード検出を試みる間隔

export function BookScanModal({ containerRef, onClose, onLogged }: BookScanModalProps) {
  const [state, setState]   = useState<ScanState>("scanning");
  const [book, setBook]     = useState<BookInfo | null>(null);
  const [manualIsbn, setManualIsbn] = useState("");
  const [priceInput, setPriceInput] = useState("");
  const [errorMsg, setErrorMsg]     = useState("");
  // ── 診断用（原因特定後に削除予定） ──
  const [debugInfo, setDebugInfo]   = useState<string>("初期化前");

  const scanCanvasRef   = useRef<HTMLCanvasElement | null>(null);
  const scanIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const zxingReaderRef  = useRef<any>(null);
  const isMountedRef    = useRef(true);

  const baseUrl = process.env.NEXT_PUBLIC_API_URL;

  // ── スキャンループ停止（モーダルクローズ時・成功時・エラー時に必ず呼ぶ） ──
  const stopScanLoop = useCallback(() => {
    if (scanIntervalRef.current) {
      clearInterval(scanIntervalRef.current);
      scanIntervalRef.current = null;
    }
  }, []);

  // ── ISBNが取れたら書誌情報を照会する ──
  const lookupIsbn = useCallback(async (isbn: string) => {
    stopScanLoop();
    setState("looking_up");
    if (!baseUrl) {
      setErrorMsg("API設定が見つかりません");
      setState("error");
      return;
    }
    try {
      const res = await fetch(`${baseUrl}/api/books/lookup?isbn=${encodeURIComponent(isbn)}`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      if (!data || !data.title) {
        setState("not_found");
        return;
      }
      setBook(data as BookInfo);
      setPriceInput(data.price != null ? String(data.price) : "");
      setState("confirm");
    } catch (err) {
      console.error("[読書通帳] ISBN照会エラー:", err);
      setErrorMsg("書誌情報の取得に失敗しました。手入力で記帳することもできます。");
      setState("error");
    }
  }, [baseUrl, stopScanLoop]);

  // ── 1フレーム分、video→canvasにコピーしてZXingに渡す ──
  const tryDecodeOneFrame = useCallback(async () => {
    const container = containerRef.current;
    const video = container?.querySelector("video") as HTMLVideoElement | null;
    if (!video) {
      setDebugInfo("video要素が見つかりません");
      return;
    }
    if (video.videoWidth === 0) {
      setDebugInfo(`video発見・しかしvideoWidth=0 (readyState=${video.readyState})`);
      return;
    }

    if (!scanCanvasRef.current) {
      scanCanvasRef.current = document.createElement("canvas");
    }
    const canvas = scanCanvasRef.current;
    canvas.width  = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      setDebugInfo("canvas context取得失敗");
      return;
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    if (!zxingReaderRef.current) {
      setDebugInfo(`video OK(${video.videoWidth}x${video.videoHeight})・しかしZXing未初期化`);
      return;
    }

    try {
      const result = await zxingReaderRef.current.decodeFromCanvas(canvas);
      if (result && isMountedRef.current) {
        const text = result.getText?.() ?? String(result);
        setDebugInfo(`検出: ${text}`);
        // ISBN-13は13桁の数字（978/979始まり）。バーコードの生テキストから抽出する。
        const digitsOnly = text.replace(/[^0-9]/g, "");
        if (digitsOnly.length === 13 && (digitsOnly.startsWith("978") || digitsOnly.startsWith("979"))) {
          lookupIsbn(digitsOnly);
        }
        // 13桁のISBN以外のバーコード（雑誌コード等）は無視して継続スキャン
      }
    } catch (err: any) {
      // デコード失敗（バーコードが視界に無い等）は通常の状態。
      // 診断用に、想定内（NotFoundException）かどうかだけ画面に出す。
      const name = err?.name || err?.constructor?.name || "unknown";
      setDebugInfo(`video OK(${video.videoWidth}x${video.videoHeight})・decode結果なし(${name})`);
    }
  }, [containerRef, lookupIsbn]);

  // ── ZXing初期化＋スキャンループ開始 ──
  useEffect(() => {
    isMountedRef.current = true;

    let cancelled = false;
    (async () => {
      try {
        setDebugInfo("ZXing読み込み中...");
        // decodeFromCanvas() は @zxing/browser 側のBrowserMultiFormatReaderにあるメソッド。
        // @zxing/library 単体には存在せず、それが先ほどのTypeErrorの原因だった
        // （実機検証で確認済み・2026-06-28）。
        const { BrowserMultiFormatReader } = await import("@zxing/browser");
        if (cancelled) return;
        zxingReaderRef.current = new BrowserMultiFormatReader();
        setDebugInfo("ZXing初期化完了。スキャンループ開始");
        scanIntervalRef.current = setInterval(tryDecodeOneFrame, SCAN_INTERVAL_MS);
      } catch (err) {
        console.error("[読書通帳] ZXing初期化エラー:", err);
        setDebugInfo(`ZXing初期化エラー: ${String(err)}`);
        if (!cancelled) {
          setErrorMsg("バーコード読み取り機能の初期化に失敗しました。手入力をご利用ください。");
          setState("error");
        }
      }
    })();

    return () => {
      cancelled = true;
      isMountedRef.current = false;
      stopScanLoop();
      // BrowserMultiFormatReaderのreset()はカメラを掴んでいる場合に内部ストリームを
      // 止めるためのものだが、今回は自前のgetUserMediaを呼んでいないため、
      // 呼んでも害はないが必須でもない。念のため呼んでおく。
      try { zxingReaderRef.current?.reset(); } catch (_) {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 手入力でのISBN照会 ──
  const handleManualLookup = () => {
    const digitsOnly = manualIsbn.replace(/[^0-9]/g, "");
    if (digitsOnly.length !== 13) {
      setErrorMsg("ISBNは13桁の数字で入力してください");
      return;
    }
    setErrorMsg("");
    lookupIsbn(digitsOnly);
  };

  // ── 記帳確定 ──
  const handleConfirmSave = async () => {
    if (!book || !baseUrl) return;
    setState("saving");
    const priceValue = priceInput.trim() ? parseInt(priceInput.trim(), 10) : null;
    try {
      const res = await fetch(`${baseUrl}/api/books/log`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          isbn: book.isbn,
          title: book.title,
          author: book.author,
          publisher: book.publisher,
          price: Number.isFinite(priceValue) ? priceValue : null,
          cover_url: book.cover_url,
        }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      onLogged({ ...book, price: Number.isFinite(priceValue) ? priceValue : book.price });
      onClose();
    } catch (err) {
      console.error("[読書通帳] 記帳保存エラー:", err);
      setErrorMsg("保存に失敗しました。もう一度お試しください。");
      setState("confirm"); // 確認画面に戻して再試行できるようにする
    }
  };

  const handleRetryScan = () => {
    setErrorMsg("");
    setBook(null);
    setState("scanning");
    if (!scanIntervalRef.current) {
      scanIntervalRef.current = setInterval(tryDecodeOneFrame, SCAN_INTERVAL_MS);
    }
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 9999,
        background: "rgba(0,0,0,0.82)",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: "24px", color: "#fff",
      }}
    >
      <div style={{ width: "100%", maxWidth: 360, textAlign: "center" }}>
        <div style={{ fontSize: 14, opacity: 0.7, marginBottom: 12 }}>📔 読書通帳に記帳</div>

        {state === "scanning" && (
          <>
            <div style={{ fontSize: 16, marginBottom: 16 }}>
              本のバーコード（ISBN）をカメラに映してください
            </div>
            <div style={{
              width: "100%", aspectRatio: "3/2", border: "2px dashed rgba(255,255,255,0.4)",
              borderRadius: 12, display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 13, opacity: 0.6, marginBottom: 16,
            }}>
              （画面奥のカメラ映像を解析中…）
            </div>
            {/* ── 診断用表示（原因特定後に削除予定） ── */}
            <div style={{
              fontSize: 11, color: "#fbbf24", marginBottom: 16, wordBreak: "break-all",
              background: "rgba(0,0,0,0.4)", padding: "8px", borderRadius: 6,
            }}>
              🔧 {debugInfo}
            </div>
          </>
        )}

        {state === "looking_up" && <div style={{ fontSize: 16 }}>書誌情報を取得中...</div>}

        {state === "not_found" && (
          <div>
            <div style={{ marginBottom: 12 }}>該当する書誌情報が見つかりませんでした。</div>
            <button onClick={handleRetryScan} style={btnStyle}>もう一度スキャン</button>
          </div>
        )}

        {state === "error" && (
          <div>
            <div style={{ marginBottom: 12, color: "#fca5a5" }}>{errorMsg}</div>
            <button onClick={handleRetryScan} style={btnStyle}>もう一度スキャン</button>
          </div>
        )}

        {(state === "scanning" || state === "error" || state === "not_found") && (
          <div style={{ marginTop: 8, fontSize: 13, opacity: 0.8 }}>
            <div style={{ marginBottom: 6 }}>バーコードが読み取れない場合：</div>
            <input
              type="text"
              inputMode="numeric"
              placeholder="ISBN（13桁）を入力"
              value={manualIsbn}
              onChange={(e) => setManualIsbn(e.target.value)}
              style={inputStyle}
            />
            <button onClick={handleManualLookup} style={{ ...btnStyle, marginTop: 8 }}>
              この番号で照会
            </button>
          </div>
        )}

        {state === "confirm" && book && (
          <div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{book.title}</div>
            {book.author && <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 4 }}>{book.author}</div>}
            {book.publisher && <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 12 }}>{book.publisher}</div>}

            <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 6, textAlign: "left" }}>
              定価（円・任意。取得できなかったため手入力できます）
            </div>
            <input
              type="number"
              placeholder="例: 1500"
              value={priceInput}
              onChange={(e) => setPriceInput(e.target.value)}
              style={inputStyle}
            />

            <button onClick={handleConfirmSave} style={{ ...btnStyle, marginTop: 16 }}>
              この内容で記帳する
            </button>
          </div>
        )}

        {state === "saving" && <div style={{ fontSize: 16 }}>記帳中...</div>}

        <button onClick={() => { stopScanLoop(); onClose(); }} style={closeBtnStyle}>
          閉じる
        </button>
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  width: "100%", padding: "12px", borderRadius: 8, border: "none",
  background: "#6366f1", color: "#fff", fontSize: 15, cursor: "pointer",
};

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "10px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.3)",
  background: "rgba(255,255,255,0.08)", color: "#fff", fontSize: 15, boxSizing: "border-box",
};

const closeBtnStyle: React.CSSProperties = {
  marginTop: 20, padding: "8px 16px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.3)",
  background: "transparent", color: "#fff", fontSize: 13, cursor: "pointer",
};
