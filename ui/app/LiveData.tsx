"use client";

import { useEffect } from "react";

/**
 * B1の成果を UI に反映（第一歩）。
 * バックエンドが生成した /data/snapshot.json（実価格＋出典・時点・2ソース照合）を読み、
 * 保有カードへ「実価格」と「2ソース照合バッジ（照合済/不一致/未照合）」を反映する。
 * MAGI判定（3審判・GENDO・碇）は判断層 B2〜B5 構築後に反映。
 */
type Holding = {
  price_display: string | null;
  reconciliation: string; // ok / mismatch / single / cached
  as_of: string | null;
  source: string | null;
  pnl: { ratio_display: string; direction: string } | null; // 含み損益（取得単価×実価格）
};
type JudgeMini = {
  judge: string;
  role: string;
  dot: string;
  verdict_word: string;
  color: string;
  dim: boolean;
  reason: string;
};
type Sizing = {
  amount_display: string;
  shares: number;
  weight_pct: number;
  price_jpy: number;
  note: string;
};
type Verification = {
  default_decision: string; // 保留 / 可
  flags: { label: string; status: string }[];
  unverified: string[];
};
type Candidate = {
  judges: JudgeMini[];
  split: string;
  gendo: string;
  sizing?: Sizing;
  verification?: Verification;
};
type Account = {
  cash: number;
  total_assets: number;
  cash_ratio: number;
  currency: string;
  positions: number;
};
type Snapshot = {
  generated_at: string;
  mode: string; // 価格ソース live/demo
  holdings_source: string; // 保有ソースの表示ラベル（サンプル/moomoo）
  usdjpy?: number;
  account?: Account;
  holdings: Record<string, Holding>;
  candidates?: Record<string, Candidate>; // {card_id: MAGI3審判}
};

const BADGE: Record<string, { text: string; cls: string }> = {
  ok: { text: "2ソース照合済", cls: "vb-ok" },
  mismatch: { text: "照合不一致", cls: "vb-warn" },
  single: { text: "未照合（単一ソース）", cls: "vb-warn" },
  cached: { text: "キャッシュ", cls: "vb-muted" },
};

export default function LiveData() {
  useEffect(() => {
    let cancelled = false;
    fetch("/data/snapshot.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((snap: Snapshot | null) => {
        if (!snap || cancelled) return;

        const holdingTickers = Object.keys(snap.holdings ?? {});
        if (holdingTickers.length === 0) {
          // 実稼働初期＝保有0（現金100%）。保有パネルは空状態にする
          const list = document.querySelector(".holdings-list");
          if (list && !list.querySelector(".holdings-empty")) {
            const total = (snap.account?.total_assets ?? 0).toLocaleString();
            list.innerHTML = `<div class="holdings-empty">保有なし（現金100% · ¥${total}）<div class="he-sub">買い候補から発注すると、ここに「予測 vs 実績」が表示されます</div></div>`;
          }
        } else {
          document.querySelectorAll<HTMLElement>(".hold").forEach((card) => {
          const ticker = card
            .querySelector(".hold-ticker")
            ?.textContent?.trim();
          if (!ticker) return;
          const h = snap.holdings[ticker];
          if (!h) return;

          const priceEl = card.querySelector<HTMLElement>(".hold-price");
          if (priceEl && h.price_display) priceEl.textContent = h.price_display;

          const pnlEl = card.querySelector<HTMLElement>(".hold-pnl");
          if (pnlEl && h.pnl) {
            pnlEl.textContent = h.pnl.ratio_display;
            pnlEl.classList.remove("up", "down");
            pnlEl.classList.add(h.pnl.direction);
          }

          const labels = card.querySelector(".hold-labels");
          if (labels && !labels.querySelector(".verify-badge")) {
            const meta = BADGE[h.reconciliation] ?? BADGE.single;
            const badge = document.createElement("span");
            badge.className = `hold-label verify-badge ${meta.cls}`;
            badge.textContent = meta.text;
            badge.title = `出典:${h.source ?? "-"} / 時点:${h.as_of ?? "-"} / 照合:${h.reconciliation}`;
            labels.appendChild(badge);
          }
          });
        }

        // 口座サマリ（現金・総資産）を反映
        if (snap.account) {
          const eq = document.querySelector(".sb-equity-value");
          if (eq) eq.textContent = `¥${snap.account.total_assets.toLocaleString()}`;
          const today = document.querySelector<HTMLElement>(".sb-equity-today");
          if (today) {
            today.textContent = "±¥0 (0.0%) 今日";
            today.classList.remove("up", "down");
          }
        }

        // 接続インジケータ（.sb-live）：保有ソースを正直に表示（サンプル/実口座の誤認防止）
        const live = document.querySelector(".sb-live");
        if (live) {
          const isReal = snap.holdings_source.startsWith("moomoo");
          live.classList.toggle("sb-live-sample", !isReal);
          live.innerHTML = `<span class="sb-live-dot"></span>${snap.holdings_source}`;
        }
        // 買い候補のMAGI 3審判を一覧ミニ（.magi-mini）へ反映
        Object.entries(snap.candidates ?? {}).forEach(([cardId, cand]) => {
          const card = document.querySelector(`.act[data-detail="${cardId}"]`);
          if (!card) return;
          // GENDO一語（判定から導出。割れなら「要検討」等）
          const gword = card.querySelector(".gendo-ind .g-word");
          if (gword) gword.textContent = cand.gendo;
          const mini = card.querySelector(".magi-mini");
          if (!mini) return;
          const mmds = mini.querySelectorAll(".mmd");
          cand.judges.forEach((j, i) => {
            const mmd = mmds[i];
            if (!mmd) return;
            const dot = mmd.querySelector(".dot");
            if (dot)
              dot.setAttribute(
                "style",
                `background:${j.dot}${j.dim ? ";opacity:0.45" : ""}`,
              );
            const vd = mmd.querySelector(".vd");
            if (vd) {
              vd.textContent = j.verdict_word;
              vd.setAttribute("style", `color:${j.color}`);
            }
          });
          const state = mini.querySelector(".magi-mini-state");
          if (state) state.textContent = cand.split;

          // 予算内サイジング（¥1M・1銘柄20%上限・米株端株）を候補カードに表示
          if (cand.sizing) {
            let sizeEl = card.querySelector<HTMLElement>(".size-rec");
            if (!sizeEl && mini.parentElement) {
              sizeEl = document.createElement("div");
              sizeEl.className = "size-rec";
              mini.parentElement.insertBefore(sizeEl, mini.nextSibling);
            }
            if (sizeEl)
              sizeEl.textContent = `推奨：${cand.sizing.amount_display}・${cand.sizing.shares}株（資産${cand.sizing.weight_pct}%）`;
          }

          // 防御層（B3）：機械照合フラグ＋決裁前ゲート（既定保留）を表示
          if (cand.verification) {
            let vEl = card.querySelector<HTMLElement>(".verify-line");
            if (!vEl && mini.parentElement) {
              vEl = document.createElement("div");
              vEl.className = "verify-line";
              mini.parentElement.appendChild(vEl);
            }
            if (vEl) {
              const flags = cand.verification.flags
                .map((f) => `${f.label} ${f.status === "ok" ? "✓" : "⚠"}`)
                .join(" ｜ ");
              const hold = cand.verification.default_decision === "保留";
              vEl.textContent = `決裁既定：${cand.verification.default_decision} ｜ ${flags}`;
              vEl.classList.toggle("vl-hold", hold);
            }
          }
        });

        // 取得時刻＝価格の話に限定（保有ソースとは分ける）
        const sb = document.querySelector(".sb-last-updated span:first-child");
        if (sb) sb.textContent = `${snap.generated_at} 取得・価格 ${snap.mode}`;
      })
      .catch(() => {
        /* スナップショット未生成時は無反映（モックのまま） */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
