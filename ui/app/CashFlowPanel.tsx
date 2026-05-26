"use client";

/**
 * 資金配分 · Allocation Panel（D-26）
 *
 * 配置：dashboard.html の action-zone と 保有 セクションの間に注入。
 * 表示：
 *   - 現状 vs 目標の3層スタック棒（Core / Satellite / Cash）
 *   - GAP（目標との差）の数値
 *   - 月次追加の配分提案（posture で上書き）
 *   - 月次追加履歴（localStorage 永続化）と「追加投入を記録」ボタン
 *
 * D-26 配分:
 *   - Tier 1 Core 60%（ETF + JP高配当）長期保持
 *   - Tier 2 Satellite 20%（ZEELE 個別株）
 *   - Tier 3 Cash 20%
 *   月次追加（既定¥30k）: posture により
 *     NEW_ENTRY_ALLOWED → 70/10/20
 *     REDUCE_ONLY → 70/0/30
 *     CASH_PRIORITY → 30/0/70
 */

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type Allocation = {
  current: { core: number; satellite: number; cash: number };
  target_pct: { core: number; satellite: number; cash: number };
  target_jpy: { core: number; satellite: number; cash: number };
  gap_jpy: { core: number; satellite: number; cash: number };
  monthly_addition_default_jpy: number;
  monthly_addition_split: { core_jpy: number; satellite_jpy: number; cash_jpy: number };
  posture_used: string;
  rule_pct: { core: number; satellite: number; cash: number };
  warnings: string[];
  note: string;
};

type Snapshot = {
  allocation?: Allocation;
  exposure?: { recommendation?: string; rationale?: string };
};

// localStorage キー（次セッションで thesis_store / DB 化）
const LS_KEY_ADDITIONS = "tradesupport:capital_additions";

type AdditionEntry = { at: string; jpy: number; note?: string };

function loadAdditions(): AdditionEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(LS_KEY_ADDITIONS);
    if (!raw) return [];
    return JSON.parse(raw) as AdditionEntry[];
  } catch {
    return [];
  }
}

function saveAdditions(arr: AdditionEntry[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LS_KEY_ADDITIONS, JSON.stringify(arr));
  } catch {
    // ignore
  }
}

const TIER_LABEL: Record<"core" | "satellite" | "cash", string> = {
  core: "Core",
  satellite: "Satellite",
  cash: "Cash",
};

const TIER_DESC: Record<"core" | "satellite" | "cash", string> = {
  core: "ETF + 高配当（長期保持）",
  satellite: "ZEELE 由来の個別株（中期）",
  cash: "防御余力（下限 20%）",
};

const POSTURE_LABEL: Record<string, string> = {
  NEW_ENTRY_ALLOWED: "新規エントリー可",
  REDUCE_ONLY: "新規控え（既存のみ）",
  CASH_PRIORITY: "現金優先",
};

export default function CashFlowPanel() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [mount, setMount] = useState<HTMLElement | null>(null);
  const [additions, setAdditions] = useState<AdditionEntry[]>([]);
  const [showInput, setShowInput] = useState(false);
  const [inputJpy, setInputJpy] = useState("30000");

  useEffect(() => {
    let canceled = false;
    let attempts = 0;
    const tick = () => {
      if (canceled) return;
      const el = document.getElementById("cashflow-panel-mount");
      if (el) {
        setMount(el);
        return;
      }
      if (attempts++ < 30) requestAnimationFrame(tick);
    };
    tick();
    return () => {
      canceled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/data/snapshot.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Snapshot | null) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setAdditions(loadAdditions());
  }, []);

  const totalAdded = useMemo(
    () => additions.reduce((s, a) => s + a.jpy, 0),
    [additions],
  );

  const recordAddition = () => {
    const jpy = Number(inputJpy.replace(/[^\d]/g, ""));
    if (!jpy || jpy < 1000) return;
    const next: AdditionEntry[] = [
      ...additions,
      { at: new Date().toISOString(), jpy },
    ];
    setAdditions(next);
    saveAdditions(next);
    setShowInput(false);
  };

  if (!mount || !data?.allocation) return null;
  const a = data.allocation;
  const total = a.current.core + a.current.satellite + a.current.cash;
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0);

  const exposure = data.exposure;

  return createPortal(
    <section className="cashflow-panel">
      <div className="cf-head">
        <div className="cf-title">
          資金配分 <span className="cf-en">Allocation</span>
          <span className="zone-tag tag-magi">MAGI 守り</span>
        </div>
        <div className="cf-total">
          総資産 <b>¥{total.toLocaleString()}</b>
          {totalAdded > 0 && (
            <span className="cf-added">
              （累計追加 ¥{totalAdded.toLocaleString()}）
            </span>
          )}
        </div>
      </div>

      {/* 現状の比率（スタック棒） */}
      <div className="cf-bar-wrap">
        <div className="cf-bar-label">現状</div>
        <div className="cf-bar">
          {(["core", "satellite", "cash"] as const).map((k) => {
            const p = pct(a.current[k]);
            if (p <= 0) return null;
            return (
              <div
                key={k}
                className={`cf-seg cf-${k}`}
                style={{ width: `${p}%` }}
                title={`${TIER_LABEL[k]} ¥${a.current[k].toLocaleString()} (${p.toFixed(0)}%)`}
              >
                <span className="cf-seg-label">
                  {TIER_LABEL[k]} {p.toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* 目標の比率（スタック棒・破線） */}
      <div className="cf-bar-wrap">
        <div className="cf-bar-label">目標</div>
        <div className="cf-bar cf-bar-target">
          {(["core", "satellite", "cash"] as const).map((k) => (
            <div
              key={k}
              className={`cf-seg cf-${k} cf-seg-target`}
              style={{ width: `${a.target_pct[k]}%` }}
              title={`${TIER_LABEL[k]} 目標 ¥${a.target_jpy[k].toLocaleString()} (${a.target_pct[k]}%)`}
            >
              <span className="cf-seg-label">
                {TIER_LABEL[k]} {a.target_pct[k]}%
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* GAP 一覧 */}
      <div className="cf-gaps">
        {(["core", "satellite", "cash"] as const).map((k) => {
          const g = a.gap_jpy[k];
          if (g === 0) return null;
          const sign = g > 0 ? "↑" : "↓";
          const cls = g > 0 ? "cf-gap-up" : "cf-gap-down";
          return (
            <div key={k} className={`cf-gap-item ${cls}`}>
              <span className="cf-gap-tier">{TIER_LABEL[k]}</span>
              <span className="cf-gap-amt">
                {sign} ¥{Math.abs(g).toLocaleString()}
              </span>
              <span className="cf-gap-desc">{TIER_DESC[k]}</span>
            </div>
          );
        })}
      </div>

      {/* posture と 月次追加の配分提案 */}
      <div className="cf-posture">
        <div className="cf-posture-row">
          <span className="cf-posture-label">今日のポスチャー</span>
          <span className={`cf-posture-val cf-posture-${a.posture_used.toLowerCase()}`}>
            {POSTURE_LABEL[a.posture_used] ?? a.posture_used}
          </span>
          {exposure?.rationale && (
            <span className="cf-posture-sub">{exposure.rationale}</span>
          )}
        </div>
      </div>

      <div className="cf-monthly">
        <div className="cf-monthly-head">
          <span className="cf-monthly-label">
            月次追加 ¥{a.monthly_addition_default_jpy.toLocaleString()} の配分提案
          </span>
          <span className="cf-monthly-rule">
            {a.rule_pct.core}/{a.rule_pct.satellite}/{a.rule_pct.cash}
          </span>
        </div>
        <div className="cf-monthly-grid">
          {(["core", "satellite", "cash"] as const).map((k) => {
            const v =
              k === "core"
                ? a.monthly_addition_split.core_jpy
                : k === "satellite"
                ? a.monthly_addition_split.satellite_jpy
                : a.monthly_addition_split.cash_jpy;
            const isZero = v === 0;
            return (
              <div key={k} className={`cf-monthly-cell cf-${k}${isZero ? " zero" : ""}`}>
                <div className="cf-monthly-tier">{TIER_LABEL[k]}</div>
                <div className="cf-monthly-amt">
                  {isZero ? "—" : `¥${v.toLocaleString()}`}
                </div>
                <div className="cf-monthly-desc">
                  {isZero ? "今は控える" : k === "core" ? "DCA 積立" : k === "satellite" ? "弾薬補充" : "バッファ"}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 警告 */}
      {a.warnings.length > 0 && (
        <div className="cf-warnings">
          {a.warnings.map((w, i) => (
            <div key={i} className="cf-warn">⚠ {w}</div>
          ))}
        </div>
      )}

      {/* 月次追加の記録 */}
      <div className="cf-actions">
        {!showInput ? (
          <button
            type="button"
            className="cf-add-btn"
            onClick={() => setShowInput(true)}
          >
            + 追加投入を記録
          </button>
        ) : (
          <div className="cf-add-form">
            <span className="cf-add-yen">¥</span>
            <input
              type="text"
              className="cf-add-input"
              value={inputJpy}
              onChange={(e) => setInputJpy(e.target.value)}
              placeholder="30000"
              autoFocus
            />
            <button type="button" className="cf-add-submit" onClick={recordAddition}>
              記録
            </button>
            <button
              type="button"
              className="cf-add-cancel"
              onClick={() => setShowInput(false)}
            >
              キャンセル
            </button>
          </div>
        )}
        {additions.length > 0 && (
          <details className="cf-history">
            <summary>履歴 {additions.length}件・累計 ¥{totalAdded.toLocaleString()}</summary>
            <ul>
              {additions
                .slice()
                .reverse()
                .map((e, i) => (
                  <li key={i}>
                    <span className="cf-hist-date">{e.at.slice(0, 10)}</span>
                    <span className="cf-hist-amt">¥{e.jpy.toLocaleString()}</span>
                    {e.note && <span className="cf-hist-note">{e.note}</span>}
                  </li>
                ))}
            </ul>
          </details>
        )}
      </div>

      <div className="cf-note">{a.note}</div>
    </section>,
    mount,
  );
}
