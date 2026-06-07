"use client";

/**
 * v2.8: Paper / 本番（楽天・手動）モードのトグル（サイドバー設置）。
 * dashboard.html の #broker-mode-mount を Portal ターゲットに使用。
 *
 * - localStorage で永続化
 * - 本番選択時に確認ダイアログ
 * - body.live-mode-active / body.paper-mode-active で全体テーマ切替
 * - 状態変更で snapshot 再生成 → リロード
 */

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Mode = "paper" | "live";

const STORAGE_KEY = "wille_broker_mode";

function applyBodyClass(mode: Mode) {
  if (typeof document === "undefined") return;
  document.body.classList.toggle("live-mode-active", mode === "live");
  document.body.classList.toggle("paper-mode-active", mode === "paper");
}

export default function BrokerModeToggle() {
  const [mount, setMount] = useState<HTMLElement | null>(null);
  const [mode, setMode] = useState<Mode>("paper");
  const [busy, setBusy] = useState(false);

  // mount point 取得
  useEffect(() => {
    let cancelled = false;
    let tries = 0;
    const tick = () => {
      if (cancelled) return;
      const el = document.getElementById("broker-mode-mount");
      if (el) {
        setMount(el);
        return;
      }
      if (tries++ < 30) requestAnimationFrame(tick);
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, []);

  // 初期値復元（localStorage + API）
  useEffect(() => {
    // 即時 localStorage から反映
    const stored = window.localStorage.getItem(STORAGE_KEY) as Mode | null;
    if (stored === "paper" || stored === "live") {
      setMode(stored);
      applyBodyClass(stored);
    } else {
      applyBodyClass("paper");
    }
    // 真の状態を API から確認
    fetch("/api/wille/broker-mode")
      .then((r) => r.json())
      .then((j) => {
        if (j?.mode === "paper" || j?.mode === "live") {
          setMode(j.mode);
          applyBodyClass(j.mode);
          window.localStorage.setItem(STORAGE_KEY, j.mode);
        }
      })
      .catch(() => {});
  }, []);

  const handleToggle = useCallback(
    async (next: Mode) => {
      if (busy || next === mode) return;
      if (next === "live") {
        const ok = window.confirm(
          "⚡ 楽天本番モードに切替えます。\n\n" +
            "・発注は楽天証券アプリで手動（このアプリは自動発注しません）\n" +
            "・約定の記録・損益が「楽天本番」の帳簿に入ります\n" +
            "・サイドバー総資産は楽天本番の残高を参照\n\n" +
            "本当に切替えますか？",
        );
        if (!ok) return;
      }
      setBusy(true);
      try {
        const res = await fetch("/api/wille/broker-mode", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ mode: next }),
        });
        const j = await res.json();
        if (j?.ok) {
          setMode(next);
          applyBodyClass(next);
          window.localStorage.setItem(STORAGE_KEY, next);
          // snapshot 再生成済 → 800ms 後にリロードで全体反映
          setTimeout(() => window.location.reload(), 800);
        } else {
          setBusy(false);
        }
      } catch {
        setBusy(false);
      }
    },
    [busy, mode],
  );

  if (!mount) return null;

  return createPortal(
    <div className="brk-mode-toggle">
      <div className="brk-mode-label">運用モード</div>
      <div className="brk-mode-row">
        <button
          type="button"
          className={`brk-mode-btn paper ${mode === "paper" ? "is-on" : ""}`}
          onClick={() => handleToggle("paper")}
          disabled={busy || mode === "paper"}
        >
          🧪 Paper
        </button>
        <button
          type="button"
          className={`brk-mode-btn live ${mode === "live" ? "is-on" : ""}`}
          onClick={() => handleToggle("live")}
          disabled={busy || mode === "live"}
        >
          ⚡ 本番
        </button>
      </div>
      <div className={`brk-mode-note ${mode}`}>
        {mode === "live"
          ? "⚡ 楽天本番（手動発注・記録）"
          : "🧪 試験運用（記録のみ・リアルマネー触らない）"}
      </div>
      <style jsx>{`
        .brk-mode-toggle {
          padding: 12px 16px;
          border-bottom: 1px solid var(--line, rgba(255, 255, 255, 0.08));
          margin-bottom: 12px;
        }
        .brk-mode-label {
          font-family: var(--mono, ui-monospace, monospace);
          font-size: 10px;
          color: var(--ink-3, #888);
          text-transform: uppercase;
          letter-spacing: 0.1em;
          margin-bottom: 8px;
        }
        .brk-mode-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 4px;
          margin-bottom: 8px;
        }
        .brk-mode-btn {
          background: rgba(255, 255, 255, 0.04);
          color: var(--ink-2, #a8a89e);
          border: 1px solid rgba(255, 255, 255, 0.1);
          padding: 8px 6px;
          font-size: 11px;
          font-weight: 700;
          border-radius: 4px;
          cursor: pointer;
          font-family: inherit;
          letter-spacing: 0.3px;
          transition: all 0.15s ease;
        }
        .brk-mode-btn:hover:not(:disabled) {
          background: rgba(255, 255, 255, 0.08);
          color: var(--ink-1, #ece9de);
        }
        .brk-mode-btn:disabled {
          cursor: default;
        }
        .brk-mode-btn.paper.is-on {
          background: linear-gradient(135deg, #4ade80, #16a085);
          color: #fff;
          border-color: transparent;
          box-shadow: 0 0 10px rgba(74, 222, 128, 0.4);
        }
        .brk-mode-btn.live.is-on {
          background: linear-gradient(135deg, #ef4444, #f59e0b);
          color: #fff;
          border-color: transparent;
          box-shadow: 0 0 14px rgba(239, 68, 68, 0.55);
        }
        .brk-mode-note {
          font-size: 10px;
          font-style: italic;
          text-align: center;
          line-height: 1.4;
        }
        .brk-mode-note.paper { color: #4ade80; }
        .brk-mode-note.live { color: #ef4444; font-weight: 700; }
      `}</style>
    </div>,
    mount,
  );
}
