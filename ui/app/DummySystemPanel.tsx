"use client";

/**
 * ダミーシステム（DS/REI・ASUKA・SHINJI・KAWORU）の最上段サマリー。
 * snapshot.dummy_system を読み、4機の収支サマリーをカード表示し、
 * クリックで保有内訳の詳細アコーディオンを開閉する。
 *
 * 価格は build_snapshot 実行時の yfinance 直近終値。ブラウザでリロード
 * （= snapshot.json 再取得）すると最新化される設計。
 * dashboard.html の #dummy-system-mount を Portal ターゲットに使用。
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Holding = {
  ticker: string;
  name: string;
  qty: number;
  buy_price: number;
  current_price: number;
  market_value: number;
  unrealized: number;
  unrealized_pct: number;
  stop_price: number;
  target_date: string | null;
  // v2.8: 単元未満株モード対応
  lot_size?: number;
  lot_equivalent_jpy?: number;
};

type Pilot = {
  name: "REI" | "ASUKA" | "SHINJI" | "KAWORU";
  label: string;
  icon: string;
  description: string;
  rule_summary: string;
  accept_stances: string[];
  max_position_pct: number;
  effective_max_pct: number;
  horizon_days: number;
  stop_loss_pct: number;
  overlay_cash_jpy: number;
  holdings_count: number;
  invested_jpy: number;
  cash_jpy: number;
  market_value_jpy: number;
  total_value_jpy: number;
  pnl_jpy: number;
  pnl_pct: number;
  holdings: Holding[];
};

type DummySystem = {
  personalities: Pilot[];
  generated_at: string;
  // v2.8: 用語整理（broker_mode = "paper" or "moomoo_live"、share_mode = "fractional" or "lot"）
  broker_mode?: "paper" | "moomoo_live";
  share_mode?: "fractional" | "lot";
  moomoo_connected?: boolean;
  moomoo_status?: {
    connected: boolean;
    sdk_available?: boolean;
    opend_reachable?: boolean;
    account_readable?: boolean;
    error?: string | null;
  } | null;
  // 旧互換
  live_mode?: boolean;
  fractional_share_mode?: boolean;
  fractional_share_note?: string;
};

type MisatoAssignment = {
  decision_id: number;
  ticker: string;
  gendo_stance: string;
  assigned_to: string;
  reason: string;
  proposed_budget_jpy: number;
  source?: "magi" | "zeele" | "both";
  preset?: string | null;
  boost?: number;
  score?: number;
  picked?: boolean;
};

type MisatoPromotion = {
  personality: string;
  n: number;
  hit_rate: number;
  avg_r: number;
  note: string;
};

type DsProposalSummary = {
  proposed: number;
  picked: number;
  avg_confidence: number;
  top_confidence: number;
};

type MisatoPlan = {
  total_budget_jpy: number;
  allocation: {
    per_pilot_jpy: Record<string, number>;
    weighted: boolean;
    reason: string;
    mode?: string;
    per_pilot_demand?: Record<string, number>;
  };
  assignments: MisatoAssignment[];
  promotions: MisatoPromotion[];
  executed: boolean;
  generated_at: string;
  picked_count?: number;
  shortlist_count?: number;
  ds_proposals?: Record<string, DsProposalSummary>;
};

type MisatoTreasury = {
  seed_jpy: number;
  allocated_jpy: number;
  available_jpy: number;
  allocations: Record<string, number>;
  deposit_count: number;
  last_deposit_at: string | null;
  updated_at: string | null;
};

type AutoTradeEntry = {
  until: string | null;
  active: boolean;
  remaining_minutes: number;
};

type AutoTradeView = {
  master: AutoTradeEntry;
  per_pilot: Record<string, AutoTradeEntry>;
  default_hours: number;
  checked_at: string;
};

type MisatoSection = {
  halted: boolean;
  halt_reason: string;
  halt_file_path: string;
  default_budget_jpy: number;
  max_per_dispatch_jpy: number;
  max_per_pilot_jpy: number;
  promotion_thresholds: { n_min: number; hit_rate_min: number; avg_r_min: number };
  plan: MisatoPlan | null;
  treasury?: MisatoTreasury;
  auto_trade?: AutoTradeView;
};

// v2.8 INVESTIGELION: WILLE 新構造（Brief + Strategy）
type Situation = "bullish" | "bearish" | "pullback" | "neutral" | "breakout" | "unknown";

type DataQualityState = "measured" | "fallback" | "unavailable" | "not_implemented";

type RitsukoBrief = {
  ticker: string;
  name?: string;
  current_price?: number | null;
  scores: {
    news_sentiment: number;
    industry: number;
    peer: number;
    event: number;
    deep: number;
  };
  boost: number;
  blocked: boolean;
  block_reason: string;
  technicals: {
    rsi: number | null;
    macd_signal: string | null;
    trend: string | null;
    situation: Situation;
  };
  news_count: number;
  industry_sector: string;
  upcoming_event_count: number;
  data_quality?: Record<string, DataQualityState>;
};

type MisatoStrategy = {
  preset: string;
  weights: {
    news: number;
    industry: number;
    peer: number;
    event: number;
    deep: number;
  };
  boost_factor: number;
};

// 後方互換: 旧フィールド型（snapshot に残っている間は読める）
type RitsukoReport = {
  ticker: string;
  situation: Situation;
  confidence: number;
  signals: string[];
  recommended_pilots: string[];
  reasoning: string;
};

type MisatoOrder = {
  ticker: string;
  assigned_to: string;
  situation: string;
  rationale: string;
  confidence: number;
};

type WilleSection = {
  ritsuko: {
    briefs?: RitsukoBrief[]; // v2.8
    reports?: RitsukoReport[]; // 後方互換
    market: { regime: string; notes?: string[] };
    situation_counts: Record<string, number>;
  };
  misato?: {
    strategy?: MisatoStrategy;
    blocked_count?: number;
    market_guard_armed?: boolean;
    outlook?: string[];
    // v2.8: 実弾モードの 1 銘柄上限
    max_lot_pct?: number | null;
    max_lot_cost_jpy?: number | null;
    treasury_for_lot_jpy?: number | null;
    affordable_count?: number;
  };
  misato_orders?: MisatoOrder[]; // 後方互換（空配列）
};

type Snapshot = {
  dummy_system?: DummySystem;
  misato?: MisatoSection;
  wille?: WilleSection;
};

const PILOT_COLOR: Record<string, string> = {
  REI: "#4a90e2",
  ASUKA: "#e74c3c",
  SHINJI: "#9b59b6",
  KAWORU: "#5d3fd3",
};

const fmtJpy = (n: number): string =>
  `¥${Math.round(n).toLocaleString("ja-JP")}`;

export default function DummySystemPanel() {
  const [mount, setMount] = useState<HTMLElement | null>(null);
  const [data, setData] = useState<DummySystem | null>(null);
  const [misato, setMisato] = useState<MisatoSection | null>(null);
  const [wille, setWille] = useState<WilleSection | null>(null);
  const [openPilot, setOpenPilot] = useState<string | null>(null);
  // 入金と予算を統合した 1 つの金額入力
  const [amountInput, setAmountInput] = useState<string>("");
  const [executeBusy, setExecuteBusy] = useState(false);
  const [depositBusy, setDepositBusy] = useState(false);
  const [haltBusy, setHaltBusy] = useState(false);
  const [actionResult, setActionResult] = useState<string | null>(null);
  // v2.8: 価格自動更新（軽量）の間隔切替（0=オフ / 30 / 60 / 300 秒）
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(300);
  const [autoRefreshing, setAutoRefreshing] = useState(false);
  // v2.8: 実弾モードの 1 銘柄上限（金額 or 割合）。ユーザーが任意に渡す
  const [maxLotMode, setMaxLotMode] = useState<"jpy" | "pct">("jpy");
  const [maxLotJpy, setMaxLotJpy] = useState<string>("100000");
  const [maxLotPct, setMaxLotPct] = useState<string>("100");

  useEffect(() => {
    let canceled = false;
    let attempts = 0;
    const tick = () => {
      if (canceled) return;
      const el = document.getElementById("dummy-system-mount");
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

  // v2.8: localStorage から自動更新間隔を復元
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem("ds_auto_refresh_sec");
    if (stored != null) {
      const n = parseInt(stored, 10);
      if ([0, 30, 60, 300].includes(n)) setAutoRefreshSec(n);
    }
    // v2.8: 1 銘柄上限の復元
    const mode = window.localStorage.getItem("ds_max_lot_mode");
    if (mode === "jpy" || mode === "pct") setMaxLotMode(mode);
    const jpy = window.localStorage.getItem("ds_max_lot_jpy");
    if (jpy) setMaxLotJpy(jpy);
    const pct = window.localStorage.getItem("ds_max_lot_pct");
    if (pct) setMaxLotPct(pct);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("ds_auto_refresh_sec", String(autoRefreshSec));
  }, [autoRefreshSec]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("ds_max_lot_mode", maxLotMode);
    window.localStorage.setItem("ds_max_lot_jpy", maxLotJpy);
    window.localStorage.setItem("ds_max_lot_pct", maxLotPct);
  }, [maxLotMode, maxLotJpy, maxLotPct]);

  // v2.8: 軽量自動更新ループ（タブが visible な時だけ走る）
  useEffect(() => {
    if (autoRefreshSec <= 0) return;
    let busy = false;
    const tick = async () => {
      if (busy) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      busy = true;
      setAutoRefreshing(true);
      try {
        await fetch("/api/refresh-prices", { method: "POST" });
        const r = await fetch(`/data/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
        if (r.ok) {
          const d: Snapshot = await r.json();
          setData(d?.dummy_system ?? null);
          setMisato(d?.misato ?? null);
          setWille(d?.wille ?? null);
        }
      } catch {
        // 静かに失敗（次のループで再試行）
      } finally {
        busy = false;
        setAutoRefreshing(false);
      }
    };
    const id = window.setInterval(tick, autoRefreshSec * 1000);
    return () => window.clearInterval(id);
  }, [autoRefreshSec]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/data/snapshot.json?t=${Date.now()}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Snapshot | null) => {
        if (cancelled) return;
        setData(d?.dummy_system ?? null);
        setMisato(d?.misato ?? null);
        setWille(d?.wille ?? null);
        // 既定金額は未配分残高 (available) を優先、なければ default_budget_jpy
        const avail = d?.misato?.treasury?.available_jpy ?? 0;
        const defaultBudget = d?.misato?.default_budget_jpy ?? 0;
        const initial = avail > 0 ? avail : defaultBudget;
        if (initial > 0) {
          setAmountInput(String(initial));
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const handleToggleHalt = async () => {
    if (!misato || haltBusy) return;
    setHaltBusy(true);
    const action = misato.halted ? "off" : "on";
    if (!misato.halted) {
      const ok = window.confirm(
        "緊急停止を有効化しますか？\n" +
          "KATSURAGI は HALT 中、すべての自動発注を停止します。",
      );
      if (!ok) {
        setHaltBusy(false);
        return;
      }
    }
    try {
      await fetch("/api/misato/halt", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action }),
      });
      window.location.reload();
    } catch {
      setHaltBusy(false);
    }
  };

  const handleDeposit = async () => {
    if (depositBusy) return;
    const amount = Number(amountInput);
    if (!Number.isFinite(amount) || amount <= 0) {
      setActionResult("入金額は正の数値で指定してください");
      return;
    }
    setDepositBusy(true);
    setActionResult("入金処理中…");
    try {
      const res = await fetch("/api/misato/deposit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ amount }),
      });
      const j = await res.json().catch(() => null);
      if (j?.ok) {
        setActionResult(`✅ 入金 ¥${amount.toLocaleString()}`);
        setTimeout(() => window.location.reload(), 500);
      } else {
        setActionResult(`エラー: ${j?.error ?? j?.stderr ?? "unknown"}`);
        setDepositBusy(false);
      }
    } catch (err) {
      setActionResult(`ネットワークエラー: ${String(err)}`);
      setDepositBusy(false);
    }
  };

  const handleWithdraw = async () => {
    if (depositBusy) return;
    const amount = Number(amountInput);
    if (!Number.isFinite(amount) || amount <= 0) {
      setActionResult("払い戻し額は正の数値で指定してください");
      return;
    }
    const ok = window.confirm(
      `KATSURAGI 預かり金から ¥${amount.toLocaleString()} を払い戻します。\n` +
        `（配分済を下回る額は払い戻しできません）\nよろしいですか？`,
    );
    if (!ok) return;
    setDepositBusy(true);
    setActionResult("払い戻し処理中…");
    try {
      const res = await fetch("/api/misato/deposit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ amount: -amount }),
      });
      const j = await res.json().catch(() => null);
      if (j?.ok) {
        setActionResult(`✅ 払い戻し ¥${amount.toLocaleString()}`);
        setTimeout(() => window.location.reload(), 500);
      } else {
        setActionResult(`エラー: ${j?.error ?? j?.stderr ?? "unknown"}`);
        setDepositBusy(false);
      }
    } catch (err) {
      setActionResult(`ネットワークエラー: ${String(err)}`);
      setDepositBusy(false);
    }
  };

  const handleAutoToggle = async (scope: string, enable: boolean) => {
    if (enable) {
      const ok = window.confirm(
        scope === "master"
          ? "🤖 マスター自動売買を ON にします（24 時間有効）。\n" +
              "全 4 機が自動で発注を行います。緊急停止は HALT で。\nよろしいですか？"
          : `🤖 ${scope} の自動売買を ON にします（24 時間有効）。\nKATSURAGI が承認なしで自動発注します。\nよろしいですか？`,
      );
      if (!ok) return;
    }
    try {
      await fetch("/api/misato/auto", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope, enabled: enable, hours: 24 }),
      });
      window.location.reload();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("auto toggle failed:", err);
    }
  };

  const handleResetTreasury = async () => {
    if (depositBusy) return;
    const ok = window.confirm(
      "KATSURAGI の預かり金と全 4 機の配分をリセットします。\n（既存の保有ポジションは閉じません）\nよろしいですか？",
    );
    if (!ok) return;
    setDepositBusy(true);
    setActionResult("リセット中…");
    try {
      await fetch("/api/misato/reset", { method: "POST" });
      window.location.reload();
    } catch {
      setDepositBusy(false);
    }
  };

  // v2.8: 1 銘柄上限の payload を組み立て（API に送信）
  const buildLotCapPayload = (): Record<string, number | string> => {
    if (maxLotMode === "jpy") {
      const v = Number(maxLotJpy);
      return Number.isFinite(v) && v > 0 ? { max_lot_jpy: v } : {};
    }
    const p = Number(maxLotPct);
    return Number.isFinite(p) && p > 0 ? { max_lot_pct: p / 100 } : {};
  };

  const handleExecute = async () => {
    if (executeBusy || !misato) return;
    const budget = Number(amountInput);
    if (!Number.isFinite(budget) || budget <= 0) {
      setActionResult("予算は正の数値で指定してください");
      return;
    }
    if (budget > misato.max_per_dispatch_jpy) {
      setActionResult(
        `1 命令上限 ¥${misato.max_per_dispatch_jpy.toLocaleString()} を超えています`,
      );
      return;
    }
    const lotCap = buildLotCapPayload();
    const lotDesc = lotCap.max_lot_jpy
      ? `1 銘柄上限 ¥${Number(lotCap.max_lot_jpy).toLocaleString()}`
      : lotCap.max_lot_pct
        ? `1 銘柄上限 ${Math.round(Number(lotCap.max_lot_pct) * 100)}% (treasury 比)`
        : "1 銘柄上限なし";
    const ok = window.confirm(
      `KATSURAGI に ¥${budget.toLocaleString()} を渡して 4 機への自動発注を実行します。\n` +
        `${lotDesc}\n` +
        "（HALT・1機上限・dry-run→approve の安全装置を経由）\nよろしいですか？",
    );
    if (!ok) return;
    setExecuteBusy(true);
    setActionResult("実行中…（最大 2 分）");
    try {
      const res = await fetch("/api/misato/execute", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ budget, ...lotCap }),
      });
      const j = await res.json().catch(() => null);
      if (j?.ok) {
        setActionResult("実行完了。snapshot を再読込します…");
        setTimeout(() => window.location.reload(), 600);
      } else {
        setActionResult(
          `エラー: ${j?.error ?? j?.dispatch_stderr ?? "unknown"}`,
        );
        setExecuteBusy(false);
      }
    } catch (err) {
      setActionResult(`ネットワークエラー: ${String(err)}`);
      setExecuteBusy(false);
    }
  };

  if (!mount || !data || data.personalities.length === 0) return null;

  const promotedSet = new Set(
    (misato?.plan?.promotions ?? []).map((p) => p.personality),
  );
  const assignmentsByPilot: Record<string, MisatoAssignment[]> = {};
  for (const a of misato?.plan?.assignments ?? []) {
    (assignmentsByPilot[a.assigned_to] ||= []).push(a);
  }

  return createPortal(
    <section className="ds-zone">
      {/* v2.10: 3 モードバナー (Paper / 単元株 Paper / 楽天本番)。本番=楽天・手動発注・記録のみ */}
      {data.broker_mode === "moomoo_live" ? (
        <div className="live-mode-banner moomoo-live" title={data.fractional_share_note}>
          <span className="live-mode-icon">⚡</span>
          <span className="live-mode-title">楽天 本番（手動）</span>
          <span className="live-mode-sub">
            発注は楽天証券アプリで手動・約定を記録（自動発注はしない）
          </span>
          <span className="moomoo-conn-badge ng">手動運用（API無し）</span>
        </div>
      ) : data.share_mode === "lot" || data.live_mode ? (
        <div className="live-mode-banner lot-paper" title={data.fractional_share_note}>
          <span className="live-mode-icon">🧪</span>
          <span className="live-mode-title">単元株モード (Paper)</span>
          <span className="live-mode-sub">
            JP 株 100 株単位で fill / DB 記録のみ（リアルマネーは扱わない）
          </span>
        </div>
      ) : data.fractional_share_mode ? (
        <div className="fractional-banner" title={data.fractional_share_note}>
          <span className="fractional-icon">⚠</span>
          <span className="fractional-title">1 株単位 Paper モード</span>
          <span className="fractional-sub">
            1 株単位 fill / DB 記録のみ。検証専用
          </span>
        </div>
      ) : null}

      {/* === 🛡 WILLE 司令室（MISATO 群の見出し）=== */}
      <div className="wille-head">
        <span className="wille-tag">🛡 WILLE</span>
        <span className="wille-head-title">司令室</span>
        <span className="wille-head-sub">
          KATSURAGI = 作戦指示・配分 / AKAGI = 銘柄分析・調査
          {misato?.treasury && (
            <>
              {" "}・ seed ¥{misato.treasury.seed_jpy.toLocaleString()} /
              未配分 ¥{misato.treasury.available_jpy.toLocaleString()}
            </>
          )}
        </span>
        {/* v2.8: 価格自動更新トグル */}
        <div className="auto-refresh-toggle" title="保有銘柄の現在価格と損益のみを軽量更新">
          {autoRefreshing && <span className="auto-refresh-spinner">⟳</span>}
          <span className="auto-refresh-label">価格自動更新</span>
          {([
            { v: 0, label: "オフ" },
            { v: 30, label: "30秒" },
            { v: 60, label: "1分" },
            { v: 300, label: "5分" },
          ] as const).map(({ v, label }) => (
            <button
              key={v}
              type="button"
              className={`auto-refresh-btn ${autoRefreshSec === v ? "is-on" : ""}`}
              onClick={() => setAutoRefreshSec(v)}
              title={v === 30 ? "30 秒は yfinance rate limit リスクあり" : ""}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* === WILLE 上段: MISATO (7) + RITSUKO (3) === */}
      <div className="wille-cols">
      {misato && (
        <div className={`wille-col wille-col-misato ds-misato ${misato.halted ? "is-halted" : ""}`}>
          <div className="ds-misato-head">
            <span className="ds-misato-icon" aria-hidden="true">🎖</span>
            <span className="ds-misato-title">
              KATSURAGI 司令
              {data.broker_mode === "moomoo_live" ? (
                <span className="ds-broker-badge live">⚡ 楽天本番</span>
              ) : (
                <span className="ds-broker-badge paper">🧪 Paper</span>
              )}
            </span>
            <span className="ds-misato-sub">
              俺が入金 → KATSURAGI が需要ベースで配分（候補のいる機にだけ寄せる）。
              安全装置: HALT / 1命令上限 ¥{misato.max_per_dispatch_jpy.toLocaleString()} / 1機上限 ¥{misato.max_per_pilot_jpy.toLocaleString()}
            </span>
            {misato.auto_trade && (
              <button
                type="button"
                className={`ds-auto-master-btn ${misato.auto_trade.master.active ? "is-on" : ""}`}
                onClick={() =>
                  handleAutoToggle("master", !misato.auto_trade!.master.active)
                }
                title={
                  misato.auto_trade.master.active
                    ? `残り ${misato.auto_trade.master.remaining_minutes} 分`
                    : "全 4 機を 24h 自動売買モードに"
                }
              >
                {misato.auto_trade.master.active
                  ? `🤖 自動ON (残${Math.floor(misato.auto_trade.master.remaining_minutes / 60)}h)`
                  : "🤖 自動売買 OFF"}
              </button>
            )}
            <button
              type="button"
              className={`ds-halt-btn ${misato.halted ? "is-on" : ""}`}
              onClick={handleToggleHalt}
              disabled={haltBusy}
              title={misato.halt_file_path}
            >
              {misato.halted ? "🟢 緊急停止を解除" : "⛔ 緊急停止"}
            </button>
          </div>

          {/* v2.8: MISATO ブロック内を 2 カラム化（左 5: 司令操作 / 右 2: 戦略パラメータ） */}
          <div className="misato-inner-grid">
            <div className="misato-inner-left">

          {misato.treasury && (
            <div className="ds-treasury">
              <div className="ds-treasury-stats">
                <div className="ds-treasury-stat">
                  <span className="ds-treasury-label">預かり金</span>
                  <span className="ds-treasury-val ds-treasury-seed">
                    {fmtJpy(misato.treasury.seed_jpy)}
                  </span>
                </div>
                <div className="ds-treasury-stat">
                  <span className="ds-treasury-label">配分済</span>
                  <span className="ds-treasury-val">
                    {fmtJpy(misato.treasury.allocated_jpy)}
                  </span>
                </div>
                <div className="ds-treasury-stat">
                  <span className="ds-treasury-label">未配分</span>
                  <span className="ds-treasury-val ds-treasury-avail">
                    {fmtJpy(misato.treasury.available_jpy)}
                  </span>
                </div>
                <div className="ds-treasury-stat" style={{ flex: "0 0 auto", opacity: 0.7 }}>
                  <span className="ds-treasury-label">入金回数</span>
                  <span className="ds-treasury-val" style={{ fontSize: 11 }}>
                    {misato.treasury.deposit_count}回 / 直近 {misato.treasury.last_deposit_at ?? "—"}
                  </span>
                </div>
              </div>

              {/* 📤 MISATO 配分内訳 + 各 DS の現状（保有銘柄 + 戦略要約） */}
              {data.personalities.length > 0 && (
                <div className="ds-alloc-block">
                  <div className="ds-alloc-head">
                    📤 各 DS の配分・戦略・保有銘柄
                  </div>
                  {data.personalities.map((p) => {
                    const up = p.pnl_jpy > 0;
                    const down = p.pnl_jpy < 0;
                    const accent = PILOT_COLOR[p.name] ?? "#888";
                    return (
                      <div key={p.name} className="ds-pilot-state" style={{ borderLeftColor: accent }}>
                        <div className="ds-pilot-state-head">
                          <span className="ds-alloc-pilot" style={{ color: accent }}>
                            {p.icon} {p.label}
                          </span>
                          <span className="ds-pilot-state-strategy">
                            {p.horizon_days}日 / Stop -{Math.round(p.stop_loss_pct * 100)}%
                            {p.accept_stances?.length > 0 && (
                              <span style={{ opacity: 0.7, marginLeft: 6 }}>· {p.accept_stances.join("/")}</span>
                            )}
                          </span>
                          <span
                            className={`ds-alloc-pnl ${up ? "up" : down ? "down" : "flat"}`}
                          >
                            {up ? "▲" : down ? "▼" : "—"} {p.pnl_jpy >= 0 ? "+" : ""}
                            {fmtJpy(p.pnl_jpy)} ({p.pnl_pct >= 0 ? "+" : ""}
                            {p.pnl_pct.toFixed(2)}%)
                          </span>
                        </div>
                        <div className="ds-pilot-state-budget">
                          配分 <b>{fmtJpy(p.overlay_cash_jpy)}</b>
                          {" → "}時価 <b>{fmtJpy(p.total_value_jpy)}</b>
                          {" · "}保有 <b>{p.holdings_count}</b> 銘柄
                          {p.cash_jpy !== p.overlay_cash_jpy && (
                            <> {" · "}現金残 {fmtJpy(p.cash_jpy)}</>
                          )}
                        </div>
                        {p.holdings.length > 0 && (
                          <div className="ds-pilot-state-holds">
                            {p.holdings.slice(0, 4).map((h) => {
                              const hUp = h.unrealized > 0;
                              const hDown = h.unrealized < 0;
                              const lotSize = h.lot_size ?? 1;
                              const lotEq = h.lot_equivalent_jpy ?? (h.current_price * lotSize);
                              return (
                                <div key={h.ticker} className="ds-pstate-hold-row">
                                  <span className="ds-pstate-hold-ticker">{h.ticker}</span>
                                  <span className="ds-pstate-hold-name">{h.name}</span>
                                  <span className="ds-pstate-hold-qty" title={lotSize > 1 ? `実弾は ${lotSize} 株単位 → ${fmtJpy(lotEq)}` : "1 株単位"}>
                                    {h.qty}株 ¥{Math.round(h.current_price).toLocaleString()}
                                    {lotSize > 1 && (
                                      <span className="ds-pstate-hold-lot">/ 実弾({lotSize}株) {fmtJpy(lotEq)}</span>
                                    )}
                                  </span>
                                  <span className={`ds-pstate-hold-pnl ${hUp ? "up" : hDown ? "down" : "flat"}`}>
                                    {hUp ? "+" : ""}{fmtJpy(h.unrealized)} ({h.unrealized_pct >= 0 ? "+" : ""}{h.unrealized_pct.toFixed(1)}%)
                                  </span>
                                </div>
                              );
                            })}
                            {p.holdings.length > 4 && (
                              <div className="ds-pstate-hold-more">
                                …他 {p.holdings.length - 4} 銘柄
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {misato.halted ? (
            <div className="ds-halt-banner">
              <strong>HALT 中</strong>：{misato.halt_reason || "KATSURAGI は全停止しています。"}
            </div>
          ) : (
            <>
              {/* v2.8: 統合フォーム — 入金/払戻し と 承認・実行 をフロー明示で分離 */}
              <div className="ds-unified-controls">
                <label className="ds-amount-input">
                  <span className="ds-amount-label">金額</span>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={misato.max_per_dispatch_jpy}
                    step={5000}
                    value={amountInput}
                    onChange={(e) => setAmountInput(e.target.value)}
                    disabled={depositBusy || executeBusy}
                    placeholder="100000"
                  />
                  <span className="ds-amount-jpy">JPY</span>
                </label>
                <div className="ds-flow-step-group">
                  <span className="ds-flow-step-label">① 預かり金に</span>
                  <button
                    type="button"
                    className="ds-deposit-btn"
                    onClick={handleDeposit}
                    disabled={depositBusy || executeBusy}
                    title="この金額を KATSURAGI に入金（seed に加算）"
                  >
                    {depositBusy ? "処理中…" : "💰 入金"}
                  </button>
                  <button
                    type="button"
                    className="ds-withdraw-btn"
                    onClick={handleWithdraw}
                    disabled={depositBusy || executeBusy}
                    title="この金額を KATSURAGI から払い戻し（配分済を下回る額は不可）"
                  >
                    {depositBusy ? "処理中…" : "💸 払戻し"}
                  </button>
                </div>
                <div className="ds-flow-step-group">
                  <span className="ds-flow-step-label">② 銘柄を買う</span>
                  <button
                    type="button"
                    className="ds-execute-btn"
                    onClick={handleExecute}
                    disabled={depositBusy || executeBusy}
                    title="この金額を予算として承認・自動発注を実行（預かり金に十分な額が必要）"
                  >
                    {executeBusy ? "実行中…" : "🚀 承認・実行"}
                  </button>
                </div>
                {actionResult && (
                  <span className="ds-execute-msg">{actionResult}</span>
                )}
              </div>

              {/* v2.8: 詳細設定（折り畳み・リセット含む） */}
              <details className="ds-advanced-settings">
                <summary>⚙ 詳細設定（1 銘柄上限・リセット）</summary>
              {/* v2.8: 1 銘柄上限（金額直接 or treasury 比割合） */}
              <div className="ds-lot-cap-controls">
                <span className="ds-lot-cap-label">1 銘柄上限</span>
                <div className="ds-lot-cap-mode-toggle">
                  <button
                    type="button"
                    className={`ds-lot-cap-mode ${maxLotMode === "jpy" ? "is-on" : ""}`}
                    onClick={() => setMaxLotMode("jpy")}
                  >
                    金額 (¥)
                  </button>
                  <button
                    type="button"
                    className={`ds-lot-cap-mode ${maxLotMode === "pct" ? "is-on" : ""}`}
                    onClick={() => setMaxLotMode("pct")}
                  >
                    割合 (%)
                  </button>
                </div>
                {maxLotMode === "jpy" ? (
                  <>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={0}
                      step={10000}
                      value={maxLotJpy}
                      onChange={(e) => setMaxLotJpy(e.target.value)}
                      placeholder="100000"
                      className="ds-lot-cap-input"
                    />
                    <span className="ds-lot-cap-unit">JPY/銘柄</span>
                  </>
                ) : (
                  <>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={100}
                      step={5}
                      value={maxLotPct}
                      onChange={(e) => setMaxLotPct(e.target.value)}
                      placeholder="100"
                      className="ds-lot-cap-input"
                    />
                    <span className="ds-lot-cap-unit">% (treasury 比)</span>
                  </>
                )}
                {wille?.misato?.max_lot_cost_jpy != null && (
                  <span className="ds-lot-cap-current">
                    現状: ¥{Math.round(wille.misato.max_lot_cost_jpy).toLocaleString()}
                    {" / 予算内 "}
                    <b>{wille.misato.affordable_count ?? 0}</b> 件
                  </span>
                )}
              </div>
                <div className="ds-advanced-reset">
                  <button
                    type="button"
                    className="ds-reset-btn"
                    onClick={handleResetTreasury}
                    disabled={depositBusy || executeBusy}
                    title="KATSURAGI 預かり金と全機の配分を 0 に戻す（保有銘柄は閉じない）"
                  >
                    ⚠ KATSURAGI を全リセット
                  </button>
                  <span className="ds-advanced-reset-note">
                    預かり金 + 配分を 0 にします（保有銘柄は残ります）。普段は使いません
                  </span>
                </div>
              </details>

              {misato.plan && (
                <div className="ds-misato-plan">
                  <div className="ds-misato-plan-head">
                    <span>
                      配分: <b>{misato.plan.allocation.mode ?? (misato.plan.allocation.weighted ? "needs-weighted" : "needs-based")}</b>
                      <small style={{ marginLeft: 6, opacity: 0.7 }}>
                        ({misato.plan.allocation.reason})
                      </small>
                    </span>
                    <span>
                      割り当て案: <b>{misato.plan.assignments.length}</b> 件
                    </span>
                    <span>
                      昇格候補:{" "}
                      <b>{misato.plan.promotions.length}</b> 機
                      <small style={{ marginLeft: 6, opacity: 0.7 }}>
                        (n≥{misato.promotion_thresholds.n_min} ∩ hit≥
                        {Math.round(misato.promotion_thresholds.hit_rate_min * 100)}%
                        ∩ R≥+{misato.promotion_thresholds.avg_r_min})
                      </small>
                    </span>
                  </div>

                  {misato.plan.promotions.length > 0 && (
                    <div className="ds-promotions">
                      {misato.plan.promotions.map((p) => (
                        <div key={p.personality} className="ds-promo">
                          🎖 <b>{p.personality}</b> 昇格推奨：n={p.n} hit_rate={Math.round(p.hit_rate * 100)}% avg_R={p.avg_r.toFixed(2)} → {p.note}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

            </>
          )}
            </div>{/* /misato-inner-left */}

            {/* === MISATO 戦略カラム（右側）=== */}
            <div className="misato-inner-right">
              {wille?.misato?.strategy ? (
                <div className="misato-strategy-panel">
                  <div className="misato-strategy-head">
                    <span className="misato-strategy-icon">🎯</span>
                    <span className="misato-strategy-title">戦略</span>
                    <span className="misato-strategy-preset">{wille.misato.strategy.preset}</span>
                  </div>
                  {(wille.misato.blocked_count ?? 0) > 0 && (
                    <div className="wille-blocked-tag">
                      例外ブロック {wille.misato.blocked_count} 件
                    </div>
                  )}
                  <div className="misato-weights-col">
                    {Object.entries(wille.misato.strategy.weights).map(([k, v]) => (
                      <div key={k} className="misato-weight-row">
                        <span className="misato-weight-label">{k}</span>
                        <span className="misato-weight-bar">
                          <span
                            className="misato-weight-fill"
                            style={{ width: `${Math.min(100, Number(v) * 100)}%` }}
                          />
                        </span>
                        <span className="misato-weight-val">{Number(v).toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="misato-strategy-note">
                    各カテゴリスコア × この重みで boost を算出 → DS 優先度補正
                  </div>
                  {(wille.misato.outlook?.length ?? 0) > 0 && (
                    <div className="misato-outlook">
                      <div className="misato-outlook-head">🔭 今後の展望・予測</div>
                      {wille.misato.outlook!.map((line, i) => (
                        <div key={i} className="misato-outlook-line">{line}</div>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </div>{/* /misato-inner-grid */}
        </div>
      )}

      {/* === RITSUKO カラム（右 3）— v2.8 新構造（Brief + 5 スコア）=== */}
      {wille && ((wille.ritsuko.briefs?.length ?? 0) > 0 || (wille.ritsuko.reports?.length ?? 0) > 0) && (
        <div className="wille-col wille-col-ritsuko">
          <div className="wille-col-head">
            <span className="wille-col-icon">🧪</span>
            <span className="wille-col-title">AKAGI</span>
            <span className="wille-col-sub">銘柄分析・5 中立スコア</span>
          </div>
          <div className="ritsuko-stat">
            <span className="ritsuko-stat-label">対象</span>
            <span className="ritsuko-stat-val">
              {wille.ritsuko.briefs?.length ?? wille.ritsuko.reports?.length ?? 0} 銘柄
            </span>
          </div>
          <div className="ritsuko-stat">
            <span className="ritsuko-stat-label">市場 regime</span>
            <span className="ritsuko-stat-val">{wille.ritsuko.market.regime}</span>
          </div>
          <div className="ritsuko-divider">状況別の銘柄数</div>
          <div className="ritsuko-sit-list">
            {[
              { key: "bullish", label: "📈 強気", cls: "up" },
              { key: "breakout", label: "🚀 ブレイク", cls: "up" },
              { key: "pullback", label: "🔄 押し目", cls: "warn" },
              { key: "neutral", label: "➖ 中立", cls: "flat" },
              { key: "bearish", label: "📉 弱気", cls: "down" },
              { key: "unknown", label: "❓ 不明", cls: "muted" },
            ].map(({ key, label, cls }) => {
              const n = wille.ritsuko.situation_counts[key] ?? 0;
              if (n === 0) return null;
              return (
                <div key={key} className={`ritsuko-sit-row ${cls}`}>
                  <span className="ritsuko-sit-label">{label}</span>
                  <span className="ritsuko-sit-count">{n}</span>
                </div>
              );
            })}
          </div>
          {/* v2.8: Brief 別ハイライト（boost 上位 + 下位）*/}
          {(wille.ritsuko.briefs?.length ?? 0) > 0 && (
            <>
              <div className="ritsuko-divider">boost 上位（買い優先）</div>
              <div className="ritsuko-brief-list">
                {[...(wille.ritsuko.briefs ?? [])]
                  .sort((a, b) => b.boost - a.boost)
                  .slice(0, 3)
                  .map((b) => (
                    <div key={b.ticker} className="ritsuko-brief-row up">
                      <span className="ritsuko-bf-ticker">{b.ticker}</span>
                      <span className="ritsuko-bf-name">{b.name ?? ""}</span>
                      <span className="ritsuko-bf-price">
                        {b.current_price != null ? `¥${Math.round(b.current_price).toLocaleString()}` : "—"}
                      </span>
                      <span className="ritsuko-bf-sit">{b.technicals.situation}</span>
                      <span className="ritsuko-bf-boost up">{b.boost >= 0 ? "+" : ""}{b.boost.toFixed(2)}</span>
                    </div>
                  ))}
              </div>
              <div className="ritsuko-divider">boost 下位（売り/見送り）</div>
              <div className="ritsuko-brief-list">
                {[...(wille.ritsuko.briefs ?? [])]
                  .sort((a, b) => a.boost - b.boost)
                  .slice(0, 3)
                  .map((b) => (
                    <div key={b.ticker} className="ritsuko-brief-row down">
                      <span className="ritsuko-bf-ticker">{b.ticker}</span>
                      <span className="ritsuko-bf-name">{b.name ?? ""}</span>
                      <span className="ritsuko-bf-price">
                        {b.current_price != null ? `¥${Math.round(b.current_price).toLocaleString()}` : "—"}
                      </span>
                      <span className="ritsuko-bf-sit">{b.technicals.situation}</span>
                      <span className="ritsuko-bf-boost down">{b.boost >= 0 ? "+" : ""}{b.boost.toFixed(2)}</span>
                    </div>
                  ))}
              </div>
              <details className="ds-wille-detail">
                <summary>📋 全銘柄 Brief（5 スコア内訳）</summary>
                <div className="ritsuko-brief-full">
                  {(wille.ritsuko.briefs ?? [])
                    .slice()
                    .sort((a, b) => b.boost - a.boost)
                    .map((b) => (
                      <div key={b.ticker} className="ritsuko-brief-full-row">
                        <div className="ritsuko-bf-head">
                          <span className="ritsuko-bf-ticker">{b.ticker}</span>
                          <span className="ritsuko-bf-name">{b.name ?? ""}</span>
                          <span className="ritsuko-bf-price">
                            {b.current_price != null ? `¥${Math.round(b.current_price).toLocaleString()}` : "—"}
                          </span>
                          <span className="ritsuko-bf-sit">{b.technicals.situation}</span>
                          <span className={`ritsuko-bf-boost ${b.boost >= 0 ? "up" : "down"}`}>
                            boost {b.boost >= 0 ? "+" : ""}{b.boost.toFixed(2)}
                          </span>
                          {b.blocked && <span className="ritsuko-bf-blocked">⛔</span>}
                        </div>
                        <div className="ritsuko-bf-scores">
                          {(["news", "industry", "peer", "event", "deep"] as const).map((k) => {
                            const labelMap: Record<string, string> = {
                              news: "news_sentiment", industry: "industry", peer: "peer", event: "event", deep: "deep",
                            };
                            const qkey = ({news: "news", industry: "industry", peer: "peer", event: "events", deep: "deep_brief"} as Record<string, string>)[k];
                            const val = b.scores[labelMap[k] as keyof typeof b.scores] ?? 0;
                            const quality = b.data_quality?.[qkey] ?? "unavailable";
                            const qSymbol = quality === "measured" ? "✓"
                              : quality === "not_implemented" ? "—"
                              : "?";
                            const qColor = quality === "measured" ? "#4ade80"
                              : quality === "not_implemented" ? "#888"
                              : "#fbbf24";
                            return (
                              <span key={k} style={{ marginRight: 8 }}>
                                {k} <b style={{ color: quality === "measured" ? "#ece9de" : "#666" }}>{val.toFixed(2)}</b>
                                <span style={{ color: qColor, marginLeft: 2, fontSize: 9 }}>{qSymbol}</span>
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                </div>
              </details>
            </>
          )}
        </div>
      )}
      </div>{/* /wille-cols */}

      {/* === ダミーシステム（DS 4 機）=== */}
      <div className="ds-section-head">
        <span className="ds-priority">検証中</span>
        <span className="ds-section-title">🤖 ダミーシステム</span>
        <span className="ds-section-sub">
          機体別評価（合算しない）・ {data.generated_at} 取得
          {misato?.treasury && (
            <>
              {" "}・ 配分済 ¥{misato.treasury.allocated_jpy.toLocaleString()}
            </>
          )}
        </span>
      </div>
      <div className="ds-grid">
        {data.personalities.map((p) => {
          const isOpen = openPilot === p.name;
          const accent = PILOT_COLOR[p.name] ?? "#888";
          const promoted = promotedSet.has(p.name);
          const myAssignments = assignmentsByPilot[p.name] ?? [];
          const autoEntry = misato?.auto_trade?.per_pilot?.[p.name];
          const autoActive =
            misato?.auto_trade?.master.active || autoEntry?.active || false;
          const isPilotSelfAuto = autoEntry?.active ?? false;
          return (
            <div
              key={p.name}
              className={`ds-card ${isOpen ? "is-open" : ""} ${promoted ? "is-promoted" : ""} ${autoActive ? "is-auto" : ""}`}
              style={{ borderTopColor: accent }}
            >
              <button
                type="button"
                className="ds-card-head"
                onClick={() =>
                  setOpenPilot((cur) => (cur === p.name ? null : p.name))
                }
                aria-expanded={isOpen}
              >
                <span className="ds-icon" aria-hidden="true">
                  {p.icon}
                </span>
                <span className="ds-label" style={{ color: accent }}>
                  {p.label}
                </span>
                {promoted && (
                  <span className="ds-promoted-badge" title="KATSURAGI 昇格推奨">
                    🎖
                  </span>
                )}
                {(() => {
                  const stat = misato?.plan?.ds_proposals?.[p.name];
                  if (!stat || stat.proposed === 0) return null;
                  return (
                    <span
                      className="ds-assign-badge"
                      title={`提案 ${stat.proposed} 件 / 採用 ${stat.picked} 件 / 平均 confidence ${stat.avg_confidence}`}
                    >
                      {stat.picked}/{stat.proposed}
                      <small style={{ marginLeft: 4, opacity: 0.7 }}>
                        c̄{stat.avg_confidence.toFixed(2)}
                      </small>
                    </span>
                  );
                })()}
                <span
                  className={`ds-pnl ${p.pnl_pct >= 0 ? "up" : "down"}`}
                  title={`分母（元本）: ${fmtJpy(p.overlay_cash_jpy)} / 損益: ${fmtJpy(p.pnl_jpy)}`}
                >
                  {p.pnl_pct >= 0 ? "+" : ""}
                  {p.pnl_pct.toFixed(2)}%
                </span>
                <span className="ds-chev" aria-hidden="true">
                  {isOpen ? "▾" : "▸"}
                </span>
              </button>

              <div className="ds-card-meta">
                <span>{p.holdings_count} 銘柄</span>
                <span
                  className="ds-card-allocated"
                  title="KATSURAGI が配分した予算（この機の元本）"
                >
                  配分 {fmtJpy(p.overlay_cash_jpy)}
                </span>
                <span
                  className={`ds-card-pnl ${p.pnl_jpy >= 0 ? "up" : "down"}`}
                  title={`時価合計: ${fmtJpy(p.total_value_jpy)} / 損益: ${fmtJpy(p.pnl_jpy)} (${p.pnl_pct.toFixed(2)}%)`}
                >
                  {p.pnl_jpy >= 0 ? "+" : ""}
                  {fmtJpy(p.pnl_jpy)} ({p.pnl_pct >= 0 ? "+" : ""}
                  {p.pnl_pct.toFixed(2)}%)
                </span>
                <button
                  type="button"
                  className={`ds-auto-pilot-btn ${isPilotSelfAuto ? "is-on" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleAutoToggle(p.name, !isPilotSelfAuto);
                  }}
                  title={
                    misato?.auto_trade?.master.active
                      ? "マスター自動売買 ON 中（個別 OFF にしてもマスターが優先）"
                      : isPilotSelfAuto
                        ? `残り ${autoEntry?.remaining_minutes ?? 0} 分`
                        : "24h 自動売買 ON"
                  }
                >
                  {isPilotSelfAuto
                    ? `🤖 ON (${Math.floor((autoEntry?.remaining_minutes ?? 0) / 60)}h)`
                    : "🤖 自動 OFF"}
                </button>
              </div>

              <div className="ds-rule" title={p.description}>
                {p.rule_summary}
              </div>

              {/* 保有銘柄 — 各銘柄 1 行（コード・社名・損益）*/}
              {p.holdings.length > 0 && (
                <div className="ds-holdings-list">
                  <div className="ds-holdings-list-head">
                    📦 保有 {p.holdings_count} 銘柄
                  </div>
                  {p.holdings.slice(0, 5).map((h) => {
                    const up = h.unrealized > 0;
                    const down = h.unrealized < 0;
                    return (
                      <div key={h.ticker} className="ds-hold-row">
                        <span className="ds-hold-ticker">{h.ticker}</span>
                        <span className="ds-hold-name">{h.name}</span>
                        <span className="ds-hold-qty">{h.qty}株</span>
                        <span
                          className={`ds-hold-pnl ${
                            up ? "up" : down ? "down" : "flat"
                          }`}
                        >
                          {up ? "+" : ""}
                          {fmtJpy(h.unrealized)} ({up ? "+" : ""}
                          {h.unrealized_pct.toFixed(2)}%)
                        </span>
                      </div>
                    );
                  })}
                  {p.holdings.length > 5 && (
                    <div className="ds-hold-more">
                      …他 {p.holdings.length - 5} 銘柄（カードクリックで全表示）
                    </div>
                  )}
                </div>
              )}

              {isOpen && (
                <div className="ds-detail">
                  <div className="ds-detail-stats">
                    <div className="ds-stat">
                      <span className="ds-stat-label">元本</span>
                      <span className="ds-stat-val">
                        {fmtJpy(p.overlay_cash_jpy)}
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">投入</span>
                      <span className="ds-stat-val">
                        {fmtJpy(p.invested_jpy)}
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">時価</span>
                      <span className="ds-stat-val">
                        {fmtJpy(p.market_value_jpy)}
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">現金残</span>
                      <span className="ds-stat-val">
                        {fmtJpy(p.cash_jpy)}
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">1銘柄上限</span>
                      <span className="ds-stat-val">
                        {Math.round(p.max_position_pct * 100)}%
                        {p.effective_max_pct !== p.max_position_pct && (
                          <small
                            style={{
                              marginLeft: 4,
                              opacity: 0.7,
                              fontSize: "10px",
                            }}
                          >
                            (現{Math.round(p.effective_max_pct * 100)}%)
                          </small>
                        )}
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">保有期間</span>
                      <span className="ds-stat-val">
                        {p.horizon_days}日
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">Stop</span>
                      <span className="ds-stat-val">
                        -{Math.round(p.stop_loss_pct * 100)}%
                      </span>
                    </div>
                    <div className="ds-stat">
                      <span className="ds-stat-label">採用stance</span>
                      <span className="ds-stat-val">
                        {p.accept_stances.join("・")}
                      </span>
                    </div>
                  </div>

                  {myAssignments.length > 0 && (() => {
                    const pickedList = myAssignments.filter((a) => a.picked);
                    const shortList = myAssignments.filter((a) => !a.picked);
                    return (
                      <div className="ds-assignments">
                        <div className="ds-assignments-head">
                          🎯 KATSURAGI 割り当て：実 fill <b>{pickedList.length}</b> 件
                          {shortList.length > 0 && (
                            <span style={{ opacity: 0.7 }}> / shortlist {shortList.length} 件</span>
                          )}
                        </div>
                        <ul>
                          {pickedList.slice(0, 8).map((a) => (
                            <li key={a.decision_id} className="ds-asn-picked">
                              <span style={{ color: "#4ade80", fontWeight: 700, fontSize: 11 }}>✅</span>
                              <span className="ds-th-ticker">{a.ticker}</span>
                              <span className={`ds-source-badge ds-source-${a.source ?? "magi"}`}>
                                {a.source === "both" ? "★ MAGI∩ZEELE" : a.source === "zeele" ? "ZEELE" : "MAGI"}
                                {a.preset && (
                                  <small style={{ marginLeft: 4, opacity: 0.8 }}>/ {a.preset}</small>
                                )}
                              </span>
                              <span style={{ opacity: 0.7, fontSize: 10 }}>
                                score={a.score?.toFixed(1)}
                              </span>
                              <span style={{ opacity: 0.85, fontWeight: 600 }}>
                                {fmtJpy(a.proposed_budget_jpy)}
                              </span>
                            </li>
                          ))}
                          {shortList.length > 0 && (
                            <li style={{ marginTop: 4, opacity: 0.5, fontSize: 10 }}>
                              ── 以下 shortlist（予算枠外・待機）──
                            </li>
                          )}
                          {shortList.slice(0, 4).map((a) => (
                            <li key={a.decision_id} className="ds-asn-short">
                              <span style={{ opacity: 0.5, fontSize: 11 }}>⏸</span>
                              <span className="ds-th-ticker" style={{ opacity: 0.6 }}>{a.ticker}</span>
                              <span className={`ds-source-badge ds-source-${a.source ?? "magi"}`} style={{ opacity: 0.6 }}>
                                {a.source === "both" ? "★" : a.source === "zeele" ? "Z" : "M"}
                              </span>
                              <span style={{ opacity: 0.5, fontSize: 10 }}>
                                score={a.score?.toFixed(1)}
                              </span>
                            </li>
                          ))}
                          {shortList.length > 4 && (
                            <li style={{ opacity: 0.4, fontSize: 10 }}>
                              ⏸ …他 {shortList.length - 4} 件
                            </li>
                          )}
                        </ul>
                      </div>
                    );
                  })()}
                  {p.holdings.length === 0 ? (
                    <div className="ds-empty">保有なし</div>
                  ) : (
                    <table className="ds-table">
                      <thead>
                        <tr>
                          <th>銘柄</th>
                          <th style={{ textAlign: "right" }}>株</th>
                          <th style={{ textAlign: "right" }}>取得</th>
                          <th style={{ textAlign: "right" }}>現在</th>
                          <th style={{ textAlign: "right" }}>含み損益</th>
                          <th style={{ textAlign: "right" }}>Stop</th>
                          <th>売却予定</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.holdings.map((h) => (
                          <tr key={h.ticker}>
                            <td>
                              <span className="ds-th-ticker">{h.ticker}</span>
                              <span className="ds-th-name">{h.name}</span>
                            </td>
                            <td style={{ textAlign: "right" }}>{h.qty}</td>
                            <td style={{ textAlign: "right" }}>
                              ¥{h.buy_price.toFixed(0)}
                            </td>
                            <td style={{ textAlign: "right" }}>
                              ¥{h.current_price.toFixed(0)}
                            </td>
                            <td
                              style={{ textAlign: "right" }}
                              className={h.unrealized_pct >= 0 ? "up" : "down"}
                            >
                              {h.unrealized_pct >= 0 ? "+" : ""}
                              {h.unrealized_pct.toFixed(2)}%
                            </td>
                            <td
                              style={{
                                textAlign: "right",
                                opacity: 0.7,
                                fontSize: "11px",
                              }}
                            >
                              ¥{h.stop_price.toFixed(0)}
                            </td>
                            <td
                              style={{
                                opacity: 0.7,
                                fontSize: "11px",
                              }}
                            >
                              {h.target_date ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <style jsx>{`
        .ds-zone {
          background: #1f1f25;
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 10px;
          padding: 16px 18px;
          margin-bottom: 16px;
        }
        .ds-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }
        .ds-title-wrap {
          display: flex;
          align-items: baseline;
          gap: 10px;
          flex-wrap: wrap;
        }
        .ds-priority {
          background: linear-gradient(135deg, #6a5acd, #5d3fd3);
          color: #fff;
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 1px;
          padding: 2px 8px;
          border-radius: 3px;
        }
        .ds-title {
          font-size: 15px;
          font-weight: 700;
          color: #ece9de;
          letter-spacing: 0.5px;
        }
        .ds-subtitle {
          font-size: 11px;
          color: #888;
        }
        .ds-total {
          display: flex;
          align-items: baseline;
          gap: 8px;
          font-size: 12px;
        }
        .ds-total-label {
          color: #888;
        }
        .ds-total-value {
          font-weight: 700;
          color: #ece9de;
          font-size: 14px;
        }
        .ds-total-pnl {
          font-weight: 600;
        }
        .ds-total-pnl.up,
        :global(.ds-zone .up) {
          color: #4ade80;
        }
        .ds-total-pnl.down,
        :global(.ds-zone .down) {
          color: #f87171;
        }
        .ds-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 12px;
        }
        .ds-card {
          background: #252530;
          border: 1px solid rgba(255, 255, 255, 0.06);
          border-top: 3px solid #888;
          border-radius: 6px;
          overflow: hidden;
          transition: border-color 0.15s ease;
        }
        .ds-card.is-open {
          border-color: rgba(255, 255, 255, 0.18);
          grid-column: 1 / -1;
        }
        .ds-card-head {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 12px 6px;
          background: transparent;
          border: none;
          color: inherit;
          font: inherit;
          cursor: pointer;
          width: 100%;
          text-align: left;
        }
        .ds-card-head:hover {
          background: rgba(255, 255, 255, 0.03);
        }
        .ds-icon {
          font-size: 18px;
        }
        .ds-label {
          font-weight: 700;
          font-size: 13px;
          flex: 1;
        }
        .ds-pnl {
          font-weight: 700;
          font-size: 13px;
        }
        .ds-chev {
          font-size: 10px;
          color: #888;
          margin-left: 4px;
        }
        .ds-card-meta {
          display: flex;
          gap: 8px;
          justify-content: space-between;
          padding: 0 12px 8px;
          font-size: 11px;
          color: #a8a89e;
        }
        .ds-rule {
          padding: 0 12px 12px;
          font-size: 11px;
          color: #888;
          line-height: 1.5;
          border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .ds-detail {
          padding: 12px;
        }
        .ds-detail-stats {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
          gap: 6px;
          margin-bottom: 12px;
          padding: 8px;
          background: rgba(0, 0, 0, 0.25);
          border-radius: 4px;
        }
        .ds-stat {
          display: flex;
          flex-direction: column;
          font-size: 10px;
        }
        .ds-stat-label {
          color: #888;
          margin-bottom: 2px;
        }
        .ds-stat-val {
          color: #ece9de;
          font-weight: 600;
          font-size: 12px;
        }
        .ds-empty {
          padding: 16px;
          text-align: center;
          color: #888;
          font-size: 12px;
        }
        .ds-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 11px;
        }
        .ds-table th,
        .ds-table td {
          padding: 6px 8px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }
        .ds-table th {
          color: #888;
          font-weight: 600;
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }
        .ds-th-ticker {
          font-family: "JetBrains Mono", ui-monospace, monospace;
          font-size: 11px;
          color: #ece9de;
          font-weight: 600;
          margin-right: 6px;
        }
        .ds-th-name {
          color: #a8a89e;
          font-size: 11px;
        }
        /* === MISATO 司令ブロック === */
        .ds-misato {
          background: rgba(93, 63, 211, 0.08);
          border: 1px solid rgba(93, 63, 211, 0.35);
          border-radius: 6px;
          padding: 10px 12px;
          margin-bottom: 12px;
        }
        .ds-misato.is-halted {
          background: rgba(248, 113, 113, 0.1);
          border-color: rgba(248, 113, 113, 0.55);
        }
        .ds-misato-head {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        .ds-misato-icon {
          font-size: 16px;
        }
        .ds-misato-title {
          font-weight: 700;
          color: #ece9de;
          font-size: 13px;
        }
        .ds-misato-sub {
          font-size: 10px;
          color: #a8a89e;
          flex: 1;
          min-width: 240px;
        }
        .ds-halt-btn {
          background: #2a2a35;
          color: #f87171;
          border: 1px solid rgba(248, 113, 113, 0.5);
          padding: 4px 10px;
          font-size: 11px;
          border-radius: 4px;
          cursor: pointer;
          font-weight: 700;
        }
        .ds-halt-btn:hover {
          background: rgba(248, 113, 113, 0.12);
        }
        .ds-halt-btn.is-on {
          background: rgba(74, 222, 128, 0.12);
          color: #4ade80;
          border-color: rgba(74, 222, 128, 0.6);
        }
        .ds-halt-btn:disabled {
          opacity: 0.5;
          cursor: wait;
        }
        .ds-halt-banner {
          margin-top: 8px;
          padding: 8px 10px;
          background: rgba(248, 113, 113, 0.15);
          color: #fda4a4;
          font-size: 12px;
          border-radius: 4px;
        }
        .ds-misato-controls {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 10px;
          flex-wrap: wrap;
        }
        .ds-misato-budget {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 11px;
          color: #a8a89e;
        }
        .ds-misato-budget input {
          background: #1a1a22;
          color: #ece9de;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 4px 8px;
          border-radius: 4px;
          width: 110px;
          font: inherit;
          font-size: 12px;
        }
        .ds-execute-btn {
          background: linear-gradient(135deg, #5d3fd3, #6a5acd);
          color: #fff;
          border: none;
          padding: 6px 14px;
          font-size: 11px;
          font-weight: 700;
          border-radius: 4px;
          cursor: pointer;
          letter-spacing: 0.5px;
        }
        .ds-execute-btn:hover {
          filter: brightness(1.1);
        }
        .ds-execute-btn:disabled {
          opacity: 0.5;
          cursor: wait;
        }
        .ds-execute-msg {
          font-size: 11px;
          color: #ece9de;
          flex: 1;
        }
        .ds-misato-plan {
          margin-top: 10px;
        }
        .ds-misato-plan-head {
          display: flex;
          gap: 16px;
          font-size: 11px;
          color: #a8a89e;
          flex-wrap: wrap;
        }
        .ds-promotions {
          margin-top: 8px;
        }
        .ds-promo {
          padding: 6px 10px;
          background: rgba(74, 222, 128, 0.1);
          border-left: 2px solid #4ade80;
          margin-bottom: 4px;
          font-size: 11px;
          color: #ece9de;
        }
        .ds-promoted-badge {
          font-size: 12px;
          margin-right: 4px;
        }
        .ds-assign-badge {
          background: rgba(93, 63, 211, 0.2);
          color: #b9a3ff;
          font-size: 9px;
          padding: 2px 6px;
          border-radius: 3px;
          font-weight: 700;
          letter-spacing: 0.5px;
        }
        .ds-card.is-promoted {
          box-shadow: 0 0 0 1px rgba(74, 222, 128, 0.4) inset;
        }
        .ds-assignments {
          background: rgba(93, 63, 211, 0.08);
          border: 1px solid rgba(93, 63, 211, 0.3);
          border-radius: 4px;
          padding: 8px 10px;
          margin-bottom: 10px;
        }
        .ds-assignments-head {
          font-size: 11px;
          color: #b9a3ff;
          font-weight: 700;
          margin-bottom: 6px;
        }
        .ds-assignments ul {
          list-style: none;
          padding: 0;
          margin: 0;
        }
        .ds-assignments li {
          display: flex;
          gap: 8px;
          align-items: center;
          font-size: 11px;
          padding: 3px 0;
          color: #ece9de;
          flex-wrap: wrap;
        }
        .ds-treasury {
          margin-top: 10px;
          background: rgba(0, 0, 0, 0.25);
          border-radius: 4px;
          padding: 10px 12px;
        }
        .ds-treasury-stats {
          display: flex;
          gap: 18px;
          margin-bottom: 8px;
          flex-wrap: wrap;
        }
        .ds-treasury-stat {
          display: flex;
          flex-direction: column;
          flex: 1;
          min-width: 100px;
        }
        .ds-treasury-label {
          font-size: 10px;
          color: #888;
          margin-bottom: 2px;
        }
        .ds-treasury-val {
          font-size: 14px;
          font-weight: 700;
          color: #ece9de;
        }
        .ds-treasury-seed {
          color: #b9a3ff;
        }
        .ds-treasury-avail {
          color: #4ade80;
        }
        .ds-treasury-controls {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .ds-deposit-btn {
          background: linear-gradient(135deg, #16a085, #1abc9c);
          color: #fff;
          border: none;
          padding: 5px 12px;
          font-size: 11px;
          font-weight: 700;
          border-radius: 4px;
          cursor: pointer;
        }
        .ds-deposit-btn:hover {
          filter: brightness(1.1);
        }
        .ds-deposit-btn:disabled {
          opacity: 0.5;
          cursor: wait;
        }
        /* v2.8: 払い戻しボタン */
        .ds-withdraw-btn {
          background: transparent;
          color: #fbbf24;
          border: 1px solid rgba(251, 191, 36, 0.55);
          padding: 5px 12px;
          font-size: 11px;
          font-weight: 700;
          border-radius: 4px;
          cursor: pointer;
        }
        .ds-withdraw-btn:hover {
          background: rgba(251, 191, 36, 0.08);
        }
        .ds-withdraw-btn:disabled {
          opacity: 0.5;
          cursor: wait;
        }
        .ds-reset-btn {
          background: transparent;
          color: #f87171;
          border: 1px solid rgba(248, 113, 113, 0.5);
          padding: 4px 10px;
          font-size: 10px;
          border-radius: 4px;
          cursor: pointer;
        }
        .ds-reset-btn:hover {
          background: rgba(248, 113, 113, 0.1);
        }
        /* === 統合フォーム（入金 + 予算 + リセット） === */
        .ds-unified-controls {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          margin-top: 10px;
          padding: 10px 12px;
          background: rgba(255, 255, 255, 0.03);
          border-radius: 4px;
          border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .ds-amount-input {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 11px;
          color: #a8a89e;
        }
        .ds-amount-label {
          font-weight: 700;
          color: #ece9de;
        }
        .ds-amount-input input {
          background: #1a1a22;
          color: #ece9de;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 6px 10px;
          border-radius: 4px;
          width: 130px;
          font: inherit;
          font-size: 13px;
          font-weight: 700;
        }
        .ds-amount-jpy {
          color: #888;
          font-size: 10px;
        }
        .ds-unified-controls .ds-deposit-btn {
          padding: 6px 14px;
          font-size: 11px;
        }
        .ds-unified-controls .ds-execute-btn {
          padding: 6px 14px;
          font-size: 11px;
        }
        .ds-unified-controls .ds-reset-btn {
          padding: 5px 10px;
          font-size: 10px;
        }
        /* === 機別カードの「配分 + 損益」目立たせる === */
        .ds-card-allocated {
          font-weight: 600;
          color: #b9a3ff;
        }
        .ds-card-pnl {
          font-weight: 700;
          font-size: 12px;
          padding: 1px 6px;
          border-radius: 3px;
        }
        .ds-card-pnl.up {
          background: rgba(74, 222, 128, 0.12);
          color: #4ade80;
        }
        .ds-card-pnl.down {
          background: rgba(248, 113, 113, 0.12);
          color: #f87171;
        }
        /* === MISATO 配分内訳（機別 1 行）=== */
        .ds-alloc-block {
          margin-top: 12px;
          padding-top: 10px;
          border-top: 1px solid rgba(255, 255, 255, 0.06);
        }
        .ds-alloc-head {
          font-size: 11px;
          color: #b9a3ff;
          font-weight: 700;
          margin-bottom: 8px;
        }
        .ds-alloc-line {
          display: grid;
          grid-template-columns: 120px 1fr auto;
          gap: 12px;
          align-items: center;
          padding: 4px 8px;
          font-size: 12px;
          border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
        }
        .ds-alloc-line:last-child { border-bottom: none; }
        .ds-alloc-pilot {
          font-weight: 700;
        }
        .ds-alloc-flow {
          color: #a8a89e;
          font-size: 11px;
        }
        .ds-alloc-pnl {
          font-weight: 700;
          font-size: 12px;
          padding: 2px 8px;
          border-radius: 3px;
          white-space: nowrap;
        }
        .ds-alloc-pnl.up { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
        .ds-alloc-pnl.down { background: rgba(248, 113, 113, 0.15); color: #f87171; }
        .ds-alloc-pnl.flat { color: #888; }
        /* === 保有銘柄リスト（各銘柄 1 行）=== */
        .ds-holdings-list {
          margin: 0 12px 10px;
          padding: 8px 10px;
          background: rgba(0, 0, 0, 0.18);
          border-radius: 4px;
          font-size: 11px;
        }
        .ds-holdings-list-head {
          color: #888;
          margin-bottom: 6px;
          font-weight: 600;
          font-size: 10px;
        }
        .ds-hold-row {
          display: grid;
          grid-template-columns: 50px 1fr auto auto;
          gap: 8px;
          align-items: center;
          padding: 3px 0;
          border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }
        .ds-hold-row:last-child { border-bottom: none; }
        .ds-hold-ticker {
          font-family: "JetBrains Mono", ui-monospace, monospace;
          color: #ece9de;
          font-weight: 700;
        }
        .ds-hold-name {
          color: #a8a89e;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 10px;
        }
        .ds-hold-qty {
          color: #888;
          font-size: 10px;
        }
        .ds-hold-pnl {
          font-weight: 700;
          font-size: 11px;
          padding: 1px 6px;
          border-radius: 3px;
          white-space: nowrap;
        }
        .ds-hold-pnl.up { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
        .ds-hold-pnl.down { background: rgba(248, 113, 113, 0.15); color: #f87171; }
        .ds-hold-pnl.flat { color: #888; }
        .ds-hold-more {
          color: #666;
          font-size: 10px;
          font-style: italic;
          padding-top: 4px;
        }
        /* === v2.7 INVESTIGELION: WILLE 司令室見出し === */
        .wille-head {
          display: flex;
          align-items: baseline;
          gap: 10px;
          padding: 6px 0 12px;
          margin-bottom: 10px;
          border-bottom: 2px solid rgba(93, 63, 211, 0.4);
          flex-wrap: wrap;
        }
        .wille-tag {
          background: linear-gradient(135deg, #5d3fd3, #4ade80);
          color: #fff;
          font-weight: 800;
          font-size: 11px;
          letter-spacing: 1px;
          padding: 3px 10px;
          border-radius: 3px;
        }
        .wille-head-title {
          font-size: 16px;
          font-weight: 700;
          color: #ece9de;
          letter-spacing: 0.5px;
        }
        .wille-head-sub {
          font-size: 11px;
          color: #888;
        }
        /* === v2.7 INVESTIGELION: WILLE 上段（MISATO 7 + RITSUKO 3）=== */
        .wille-cols {
          display: grid;
          grid-template-columns: 7fr 3fr;
          gap: 12px;
          margin-bottom: 16px;
          align-items: stretch;  /* 両カラムを同じ高さに揃える */
        }
        @media (max-width: 900px) {
          .wille-cols { grid-template-columns: 1fr; }
        }
        .wille-col {
          background: rgba(0, 0, 0, 0.2);
          border-radius: 6px;
        }
        .wille-col-misato {
          /* ds-misato の既存スタイルを継承するため、最小上書きのみ */
          margin-bottom: 0;
          display: flex;
          flex-direction: column;
        }
        .wille-col-ritsuko {
          background: linear-gradient(135deg, rgba(78, 205, 196, 0.06), rgba(93, 63, 211, 0.04));
          border: 1px solid rgba(78, 205, 196, 0.25);
          padding: 12px 14px;
          display: flex;
          flex-direction: column;
          gap: 6px;
          max-height: 100%;
          overflow-y: auto;  /* MISATO 側が伸びても RITSUKO は内部スクロール */
        }
        .wille-col-head {
          display: flex;
          align-items: baseline;
          gap: 8px;
          padding-bottom: 8px;
          margin-bottom: 6px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        .wille-col-icon { font-size: 16px; }
        .wille-col-title {
          font-weight: 700;
          color: #ece9de;
          font-size: 13px;
        }
        .wille-col-sub {
          font-size: 10px;
          color: #888;
        }
        .ritsuko-stat {
          display: flex;
          justify-content: space-between;
          font-size: 11px;
          padding: 3px 0;
        }
        .ritsuko-stat-label { color: #888; }
        .ritsuko-stat-val { color: #ece9de; font-weight: 600; }
        .ritsuko-divider {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px dashed rgba(255, 255, 255, 0.06);
          font-size: 10px;
          color: #888;
          letter-spacing: 0.5px;
        }
        .ritsuko-sit-list {
          display: flex;
          flex-direction: column;
          gap: 2px;
          margin-top: 4px;
        }
        .ritsuko-sit-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 11px;
          padding: 3px 6px;
          border-radius: 3px;
          background: rgba(255, 255, 255, 0.03);
        }
        .ritsuko-sit-row.up { background: rgba(74, 222, 128, 0.1); color: #4ade80; }
        .ritsuko-sit-row.down { background: rgba(248, 113, 113, 0.1); color: #f87171; }
        .ritsuko-sit-row.warn { background: rgba(251, 191, 36, 0.1); color: #fbbf24; }
        .ritsuko-sit-row.flat { color: #a8a89e; }
        .ritsuko-sit-row.muted { color: #666; }
        .ritsuko-sit-label { font-weight: 500; }
        .ritsuko-sit-count { font-weight: 700; font-size: 13px; }
        /* v2.8: 単元未満株モードバナー */
        .fractional-banner {
          display: flex;
          align-items: center;
          gap: 10px;
          background: rgba(251, 191, 36, 0.08);
          border: 1px solid rgba(251, 191, 36, 0.35);
          border-radius: 6px;
          padding: 6px 12px;
          margin-bottom: 10px;
          font-size: 11px;
        }
        .fractional-icon { font-size: 14px; color: #fbbf24; }
        .fractional-title {
          font-weight: 700;
          color: #fbbf24;
          letter-spacing: 0.3px;
        }
        .fractional-sub {
          color: #a8a89e;
          font-size: 10px;
        }
        .ds-pstate-hold-lot {
          color: #fbbf24;
          font-size: 9px;
          margin-left: 4px;
          opacity: 0.8;
        }
        /* v2.8: フローステップグループ（入金/実行を明示） */
        .ds-flow-step-group {
          display: flex;
          align-items: center;
          gap: 4px;
          padding: 4px 8px;
          background: rgba(93, 63, 211, 0.05);
          border: 1px dashed rgba(93, 63, 211, 0.25);
          border-radius: 4px;
        }
        .ds-flow-step-label {
          font-size: 10px;
          color: #b9a3ff;
          font-weight: 700;
          margin-right: 2px;
        }
        /* v2.8: 詳細設定（折り畳み） */
        .ds-advanced-settings {
          margin-top: 10px;
          padding: 8px 12px;
          background: rgba(0, 0, 0, 0.18);
          border-radius: 4px;
          border: 1px solid rgba(255, 255, 255, 0.06);
        }
        .ds-advanced-settings summary {
          cursor: pointer;
          font-size: 11px;
          color: #888;
          padding: 2px 0;
          font-weight: 600;
        }
        .ds-advanced-settings summary:hover { color: #ece9de; }
        .ds-advanced-reset {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px dashed rgba(255, 255, 255, 0.06);
        }
        .ds-advanced-reset-note {
          font-size: 10px;
          color: #888;
        }
        /* v2.8: 1 銘柄上限コントロール */
        .ds-lot-cap-controls {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
          margin-top: 8px;
          padding: 8px 12px;
          background: rgba(74, 222, 128, 0.04);
          border: 1px dashed rgba(74, 222, 128, 0.35);
          border-radius: 4px;
        }
        .ds-lot-cap-label {
          font-size: 11px;
          color: #ece9de;
          font-weight: 700;
          margin-right: 4px;
        }
        .ds-lot-cap-mode-toggle { display: flex; gap: 2px; }
        .ds-lot-cap-mode {
          background: #2a2a35;
          color: #a8a89e;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 3px 8px;
          font-size: 10px;
          border-radius: 3px;
          cursor: pointer;
        }
        .ds-lot-cap-mode.is-on {
          background: linear-gradient(135deg, #4ade80, #16a085);
          color: #fff;
          border-color: transparent;
          font-weight: 700;
        }
        .ds-lot-cap-input {
          background: #1a1a22;
          color: #ece9de;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 4px 8px;
          border-radius: 3px;
          width: 100px;
          font: inherit;
          font-size: 12px;
          font-weight: 700;
        }
        .ds-lot-cap-unit { color: #888; font-size: 10px; }
        .ds-lot-cap-current {
          margin-left: auto;
          font-size: 10px;
          color: #4ade80;
          font-weight: 600;
        }
        /* v2.8: モードバナー（3 種） */
        .live-mode-banner {
          display: flex;
          align-items: center;
          gap: 10px;
          border-radius: 6px;
          padding: 8px 14px;
          margin-bottom: 10px;
          font-size: 11px;
        }
        .live-mode-banner.lot-paper {
          background: linear-gradient(135deg, rgba(74, 222, 128, 0.06), rgba(93, 63, 211, 0.04));
          border: 1px solid rgba(74, 222, 128, 0.35);
        }
        .live-mode-banner.lot-paper .live-mode-title { color: #4ade80; }
        .live-mode-banner.moomoo-live {
          background: linear-gradient(135deg, rgba(248, 113, 113, 0.10), rgba(251, 191, 36, 0.08));
          border: 2px solid rgba(248, 113, 113, 0.5);
        }
        .live-mode-banner.moomoo-live .live-mode-title { color: #f87171; }
        .live-mode-icon { font-size: 16px; }
        .live-mode-title {
          font-weight: 800;
          letter-spacing: 0.5px;
        }
        .live-mode-sub { color: #ece9de; font-size: 10px; flex: 1; }
        .moomoo-conn-badge {
          font-size: 10px;
          font-weight: 700;
          padding: 3px 8px;
          border-radius: 3px;
          margin-left: auto;
        }
        .moomoo-conn-badge.ok { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
        .moomoo-conn-badge.ng { background: rgba(248, 113, 113, 0.15); color: #f87171; }
        /* 総資産横の Paper/Live バッジ */
        .ds-broker-badge {
          display: inline-block;
          font-size: 10px;
          font-weight: 800;
          padding: 2px 8px;
          border-radius: 3px;
          margin-left: 8px;
          letter-spacing: 0.5px;
        }
        .ds-broker-badge.paper {
          background: rgba(78, 205, 196, 0.18);
          color: #4ecdc4;
          border: 1px solid rgba(78, 205, 196, 0.35);
        }
        .ds-broker-badge.live {
          background: rgba(248, 113, 113, 0.18);
          color: #f87171;
          border: 1px solid rgba(248, 113, 113, 0.5);
        }
        /* v2.8: 自動更新トグル */
        .auto-refresh-toggle {
          display: flex;
          align-items: center;
          gap: 4px;
          margin-left: auto;
          font-size: 10px;
        }
        .auto-refresh-spinner {
          color: #4ade80;
          font-size: 12px;
          animation: spin 1s linear infinite;
        }
        @keyframes spin { from {transform: rotate(0)} to {transform: rotate(360deg)} }
        .auto-refresh-label {
          color: #888;
          margin-right: 4px;
        }
        .auto-refresh-btn {
          background: #2a2a35;
          color: #a8a89e;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 2px 8px;
          font-size: 10px;
          border-radius: 3px;
          cursor: pointer;
          font-family: inherit;
        }
        .auto-refresh-btn:hover {
          background: rgba(255, 255, 255, 0.06);
          color: #ece9de;
        }
        .auto-refresh-btn.is-on {
          background: linear-gradient(135deg, #5d3fd3, #4ade80);
          color: #fff;
          border-color: transparent;
          font-weight: 700;
        }
        /* v2.8: 各 DS の状態行（MISATO ブロック内）*/
        .ds-pilot-state {
          background: rgba(0, 0, 0, 0.18);
          border-left: 3px solid #888;
          border-radius: 4px;
          padding: 6px 10px;
          margin-bottom: 6px;
        }
        .ds-pilot-state-head {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 4px;
        }
        .ds-pilot-state-strategy {
          font-size: 10px;
          color: #888;
        }
        .ds-pilot-state-head .ds-alloc-pilot {
          font-weight: 700;
          font-size: 12px;
        }
        .ds-pilot-state-head .ds-alloc-pnl {
          margin-left: auto;
        }
        .ds-pilot-state-budget {
          font-size: 11px;
          color: #a8a89e;
          margin-bottom: 4px;
        }
        .ds-pilot-state-budget b { color: #ece9de; }
        .ds-pilot-state-holds {
          display: flex;
          flex-direction: column;
          gap: 1px;
          margin-top: 4px;
          padding-top: 4px;
          border-top: 1px dashed rgba(255, 255, 255, 0.05);
        }
        .ds-pstate-hold-row {
          display: grid;
          grid-template-columns: 50px 1fr auto auto;
          gap: 8px;
          align-items: center;
          font-size: 10px;
          padding: 2px 0;
        }
        .ds-pstate-hold-ticker {
          color: #ece9de;
          font-weight: 700;
          font-family: "JetBrains Mono", ui-monospace, monospace;
        }
        .ds-pstate-hold-name {
          color: #888;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .ds-pstate-hold-qty {
          color: #a8a89e;
          font-size: 10px;
          font-family: "JetBrains Mono", ui-monospace, monospace;
        }
        .ds-pstate-hold-pnl {
          font-size: 10px;
          font-weight: 700;
          padding: 1px 6px;
          border-radius: 3px;
        }
        .ds-pstate-hold-pnl.up { background: rgba(74, 222, 128, 0.12); color: #4ade80; }
        .ds-pstate-hold-pnl.down { background: rgba(248, 113, 113, 0.12); color: #f87171; }
        .ds-pstate-hold-pnl.flat { color: #888; }
        .ds-pstate-hold-more {
          color: #666;
          font-size: 10px;
          font-style: italic;
          padding-top: 2px;
        }
        /* v2.8: MISATO ブロック内を 2 カラム化（司令 5 : 戦略 2）*/
        .misato-inner-grid {
          display: grid;
          grid-template-columns: 5fr 2fr;
          gap: 12px;
          margin-top: 8px;
        }
        @media (max-width: 900px) {
          .misato-inner-grid { grid-template-columns: 1fr; }
        }
        .misato-inner-left { min-width: 0; }
        .misato-inner-right { min-width: 0; }
        .misato-strategy-panel {
          background: rgba(93, 63, 211, 0.08);
          border: 1px solid rgba(93, 63, 211, 0.25);
          border-radius: 6px;
          padding: 10px 12px;
          height: 100%;
          display: flex;
          flex-direction: column;
        }
        .misato-strategy-head {
          display: flex;
          align-items: baseline;
          gap: 8px;
          padding-bottom: 6px;
          margin-bottom: 6px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        .misato-strategy-icon { font-size: 14px; }
        .misato-strategy-title {
          font-size: 12px;
          font-weight: 700;
          color: #ece9de;
        }
        .misato-strategy-preset {
          font-size: 11px;
          color: #b9a3ff;
          font-weight: 700;
          margin-left: auto;
        }
        .misato-weights-col {
          display: flex;
          flex-direction: column;
          gap: 4px;
          margin-top: 4px;
        }
        .misato-weight-row {
          display: grid;
          grid-template-columns: 56px 1fr 36px;
          gap: 6px;
          align-items: center;
          font-size: 10px;
        }
        .misato-weight-label {
          color: #888;
          text-transform: lowercase;
        }
        .misato-weight-bar {
          display: block;
          height: 6px;
          background: rgba(255, 255, 255, 0.05);
          border-radius: 3px;
          overflow: hidden;
        }
        .misato-weight-fill {
          display: block;
          height: 100%;
          background: linear-gradient(90deg, #5d3fd3, #b9a3ff);
        }
        .misato-weight-val {
          text-align: right;
          color: #ece9de;
          font-weight: 700;
          font-size: 11px;
        }
        .misato-strategy-note {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px dashed rgba(255, 255, 255, 0.06);
          font-size: 10px;
          color: #888;
          font-style: italic;
          line-height: 1.4;
        }
        .misato-outlook {
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px solid rgba(93, 63, 211, 0.25);
        }
        .misato-outlook-head {
          font-size: 11px;
          font-weight: 700;
          color: #b9a3ff;
          margin-bottom: 6px;
        }
        .misato-outlook-line {
          font-size: 10px;
          color: #ece9de;
          padding: 3px 0;
          line-height: 1.5;
          border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
        }
        .misato-outlook-line:last-child { border-bottom: none; }
        /* v2.8: MISATO 戦略パラメータ表示 (旧) */
        .wille-misato-strategy {
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px dashed rgba(93, 63, 211, 0.3);
        }
        .wille-weights-row {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin: 6px 0;
        }
        .wille-weight-chip {
          font-size: 11px;
          padding: 2px 8px;
          border-radius: 3px;
          background: rgba(93, 63, 211, 0.15);
          color: #b9a3ff;
        }
        .wille-blocked-tag {
          margin-left: 10px;
          font-size: 10px;
          padding: 2px 6px;
          border-radius: 3px;
          background: rgba(248, 113, 113, 0.15);
          color: #f87171;
          font-weight: 700;
        }
        .wille-orders-note {
          font-size: 10px;
          color: #888;
          margin-top: 4px;
          font-style: italic;
        }
        /* v2.8: RITSUKO Brief 表示 */
        .ritsuko-brief-list {
          display: flex;
          flex-direction: column;
          gap: 2px;
          margin-top: 4px;
        }
        .ritsuko-brief-row {
          display: grid;
          grid-template-columns: 50px 1fr auto auto auto;
          gap: 6px;
          font-size: 10px;
          padding: 3px 6px;
          border-radius: 3px;
          background: rgba(255, 255, 255, 0.03);
          align-items: center;
        }
        .ritsuko-bf-name {
          color: #a8a89e;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 10px;
        }
        .ritsuko-bf-price {
          color: #ece9de;
          font-weight: 700;
          font-size: 10px;
          font-family: "JetBrains Mono", ui-monospace, monospace;
        }
        .ritsuko-brief-row.up { background: rgba(74, 222, 128, 0.08); }
        .ritsuko-brief-row.down { background: rgba(248, 113, 113, 0.08); }
        .ritsuko-bf-ticker { color: #ece9de; font-weight: 700; font-family: "JetBrains Mono", ui-monospace, monospace; }
        .ritsuko-bf-sit { color: #888; font-size: 10px; }
        .ritsuko-bf-boost { font-weight: 700; }
        .ritsuko-bf-boost.up { color: #4ade80; }
        .ritsuko-bf-boost.down { color: #f87171; }
        .ritsuko-bf-blocked { color: #f87171; font-size: 10px; }
        .ritsuko-brief-full {
          margin-top: 6px;
          max-height: 360px;
          overflow-y: auto;
          font-size: 11px;
        }
        .ritsuko-brief-full-row {
          padding: 4px 6px;
          border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
        }
        .ritsuko-bf-head {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-bottom: 2px;
        }
        .ritsuko-bf-scores {
          font-size: 10px;
          color: #888;
          padding-left: 4px;
        }
        /* MISATO 司令ブロック内の作戦指示 */
        .wille-misato-orders {
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px dashed rgba(93, 63, 211, 0.3);
        }
        .wille-orders-head {
          font-size: 11px;
          color: #b9a3ff;
          font-weight: 700;
          margin-bottom: 6px;
        }
        /* ダミーシステム セクションヘッダ */
        .ds-section-head {
          display: flex;
          align-items: baseline;
          gap: 10px;
          padding: 10px 0 12px;
          margin-bottom: 4px;
          border-top: 1px solid rgba(255, 255, 255, 0.06);
          margin-top: 4px;
          flex-wrap: wrap;
        }
        .ds-section-title {
          font-size: 14px;
          font-weight: 700;
          color: #ece9de;
        }
        .ds-section-sub {
          font-size: 11px;
          color: #888;
        }
        /* (legacy 互換) ds-wille-section は不要だが念のため残す */
        .ds-wille-section {
          background: linear-gradient(135deg, rgba(93, 63, 211, 0.06), rgba(74, 222, 128, 0.04));
          border: 1px solid rgba(93, 63, 211, 0.25);
          border-radius: 6px;
          padding: 14px 16px;
          margin-bottom: 12px;
        }
        .ds-wille-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 14px;
          margin-bottom: 10px;
        }
        @media (max-width: 768px) {
          .ds-wille-row { grid-template-columns: 1fr; }
        }
        .ds-block-head {
          display: flex;
          align-items: baseline;
          gap: 8px;
          margin-bottom: 8px;
        }
        .ds-block-icon { font-size: 16px; }
        .ds-block-title {
          font-weight: 700;
          font-size: 13px;
          color: #ece9de;
        }
        .ds-block-sub {
          font-size: 10px;
          color: #888;
          margin-left: auto;
        }
        .ds-situation-chips, .ds-order-chips {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
        }
        .ds-sit-chip {
          font-size: 11px;
          padding: 3px 8px;
          border-radius: 12px;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .ds-sit-chip.up { background: rgba(74, 222, 128, 0.12); color: #4ade80; border-color: rgba(74, 222, 128, 0.3); }
        .ds-sit-chip.down { background: rgba(248, 113, 113, 0.12); color: #f87171; border-color: rgba(248, 113, 113, 0.3); }
        .ds-sit-chip.warn { background: rgba(251, 191, 36, 0.12); color: #fbbf24; border-color: rgba(251, 191, 36, 0.3); }
        .ds-sit-chip.flat { color: #a8a89e; }
        .ds-sit-chip.muted { color: #666; }
        .ds-order-chip {
          font-size: 11px;
          padding: 3px 8px;
          border-radius: 12px;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid #444;
          color: #ece9de;
        }
        .ds-wille-detail {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px dashed rgba(255, 255, 255, 0.08);
        }
        .ds-wille-detail summary {
          cursor: pointer;
          font-size: 11px;
          color: #a8a89e;
          padding: 4px 0;
        }
        .ds-wille-detail summary:hover { color: #ece9de; }
        .ds-wille-list {
          margin-top: 8px;
          max-height: 320px;
          overflow-y: auto;
          font-family: "JetBrains Mono", ui-monospace, monospace;
          font-size: 11px;
        }
        .ds-wille-row-item {
          display: grid;
          grid-template-columns: 56px 80px 90px 70px 1fr;
          gap: 8px;
          align-items: center;
          padding: 3px 6px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }
        .ds-wille-row-item.dimmed { opacity: 0.5; }
        .ds-ww-ticker { color: #ece9de; font-weight: 700; }
        .ds-ww-situation { padding: 1px 6px; border-radius: 3px; font-weight: 600; font-size: 10px; text-align: center; }
        .ds-ww-situation.up { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
        .ds-ww-situation.down { background: rgba(248, 113, 113, 0.15); color: #f87171; }
        .ds-ww-situation.warn { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
        .ds-ww-situation.flat { color: #888; }
        .ds-ww-pilot { font-weight: 700; font-size: 11px; }
        .ds-ww-conf { color: #888; font-size: 10px; }
        .ds-ww-signals { color: #a8a89e; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        /* === 自動売買トグル === */
        .ds-auto-master-btn {
          background: #2a2a35;
          color: #a8a89e;
          border: 1px solid rgba(255, 255, 255, 0.15);
          padding: 4px 10px;
          font-size: 11px;
          font-weight: 700;
          border-radius: 4px;
          cursor: pointer;
        }
        .ds-auto-master-btn:hover {
          background: rgba(255, 255, 255, 0.04);
        }
        .ds-auto-master-btn.is-on {
          background: linear-gradient(135deg, #16a085, #1abc9c);
          color: #fff;
          border-color: #16a085;
        }
        .ds-auto-pilot-btn {
          background: transparent;
          color: #888;
          border: 1px solid rgba(255, 255, 255, 0.12);
          padding: 2px 8px;
          font-size: 9px;
          font-weight: 700;
          border-radius: 3px;
          cursor: pointer;
          margin-left: auto;
        }
        .ds-auto-pilot-btn.is-on {
          background: rgba(22, 160, 133, 0.2);
          color: #4ade80;
          border-color: #16a085;
        }
        .ds-card.is-auto {
          box-shadow: 0 0 0 1px rgba(22, 160, 133, 0.5) inset, 0 0 8px rgba(22, 160, 133, 0.15);
        }
        /* === ソースバッジ === */
        .ds-source-badge {
          font-size: 9px;
          font-weight: 700;
          padding: 1px 5px;
          border-radius: 3px;
          letter-spacing: 0.4px;
        }
        .ds-source-magi {
          background: rgba(255, 140, 66, 0.18);
          color: #ff8c42;
        }
        .ds-source-zeele {
          background: rgba(78, 205, 196, 0.18);
          color: #4ecdc4;
        }
        .ds-source-both {
          background: linear-gradient(135deg, rgba(255, 140, 66, 0.25), rgba(78, 205, 196, 0.25));
          color: #fff7e0;
          box-shadow: 0 0 4px rgba(255, 255, 255, 0.2);
        }
      `}</style>
    </section>,
    mount,
  );
}
