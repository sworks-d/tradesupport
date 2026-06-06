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
  current_price?: number | null;
  reconciliation: string; // ok / mismatch / single / cached
  as_of: string | null;
  source: string | null;
  pnl: { ratio_display: string; direction: string } | null; // 含み損益（取得単価×実価格）
  // 保有カード拡充（build_snapshot holdings から）
  qty?: number;
  cost_price?: number;
  cost_jpy?: number;
  unrealized_jpy?: number | null;
  name?: string;
  sector?: string;
  market?: string;
  target_pct?: number | null;
  stop_pct?: number | null;
  buy_date?: string | null;
  strategy?: string;
  thesis?: string;
  target_period_days?: number | null;
  history_30d?: number[];
  // X-2B holding_health（Kanchi T1-T5 翻案）
  health?: {
    state: "OK" | "WARN" | "REVIEW";
    triggers_fired: string[];
    evidence: { trigger_id: string; state: string; reason: string }[];
  };
};

const HEALTH_BADGE: Record<
  "OK" | "WARN" | "REVIEW",
  { text: string; cls: string; color: string }
> = {
  OK: { text: "規律OK", cls: "vb-ok", color: "#16a085" },
  WARN: { text: "規律WARN", cls: "vb-warn", color: "#e67e22" },
  REVIEW: { text: "規律REVIEW", cls: "vb-warn", color: "#c0392b" },
};
type JudgeMini = {
  judge: string;
  role: string;
  dot: string;
  verdict: string;
  verdict_word: string;
  color: string;
  dim: boolean;
  reason: string;
  counter?: string[]; // 自領域の反証（B群／信用性）
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
type Commander = {
  recommendation: string;
  counter: string;
  src_note: string;
  compliant: boolean;
};
// GENDO推奨カード（初心者コーチ・守り主導・攻めは灰色）。build_snapshot の gendo_card に対応。
type GendoCardT = {
  action: string;
  sleeve: string;
  reason: string;
  counter: string;
  guardrail: string;
  defense_confidence: string;
  offense_confidence: string;
  learn_note: string;
};
type Candidate = {
  judges: JudgeMini[];
  split: string;
  split_interp?: string;
  gendo: string;
  sizing?: Sizing;
  verification?: Verification;
  commander?: Commander;
  gendo_card?: GendoCardT;
};
type Account = {
  cash: number;
  total_assets: number;
  cash_ratio: number;
  currency: string;
  positions: number;
};
// v2.10: 集中投資 KPI サマリー（D 案: ¥100k 検証用）
type ConcentrationKpi = {
  seed_jpy: number;
  invested_jpy: number;
  market_value_jpy: number;
  cash_reserve_jpy: number;
  unrealized_pnl_jpy: number;
  unrealized_pnl_pct: number;
  cumulative_return_jpy: number;
  cumulative_return_pct: number;
  position_count: number;
  cash_reserve_pct: number;
  investment_pct: number;
};
// v2.10 Phase 2 Mini: 判断精度
type JudgmentBreakdown = {
  total: number;
  evaluated: number;
  pending: number;
  hits: number;
  misses: number;
  neutrals: number;
  accuracy_pct: number | null;
  avg_return_pct: number | null;
};
type JudgmentAccuracy = {
  lookback_days: number;
  status: "active" | "insufficient_data" | "error";
  min_samples_required: number;
  overall: JudgmentBreakdown;
  by_source: { magi: JudgmentBreakdown; zeele: JudgmentBreakdown };
  by_pilot: Record<string, JudgmentBreakdown>;
};
// v2.10 Phase 1: ポートフォリオ相関分析
type CorrelationAnalysis = {
  status: "active" | "insufficient_data" | "error";
  tickers?: string[];
  matrix?: number[][];
  high_correlation_pairs?: { a: string; b: string; correlation: number }[];
  max_corr?: number | null;
  mean_abs_corr?: number | null;
  concentration_label?: string;
};
// v2.10 Phase 4: テーマ強度
type ThemeStrength = {
  status: "active" | "no_matches" | "insufficient_data" | "error";
  themes?: Record<
    string,
    {
      mentions_7d: number;
      mentions_30d: number;
      momentum: number | null;
      intensity: number;
    }
  >;
  total_topics_30d?: number;
  top_themes?: string[];
};
// v2.10 Phase 5: factor exposure
type FactorExposure = {
  status: "active" | "insufficient_data" | "error";
  weighted_factors?: {
    momentum: number | null;
    value: number | null;
    quality: number | null;
    size: number | null;
  };
  concentration_label?: string;
  max_factor?: string | null;
};
type Snapshot = {
  generated_at: string;
  mode: string; // 価格ソース live/demo
  holdings_source: string; // 保有ソースの表示ラベル（サンプル/moomoo）
  usdjpy?: number;
  account?: Account;
  holdings: Record<string, Holding>;
  candidates?: Record<string, Candidate>; // {card_id: MAGI3審判}
  dummy_system?: {
    concentration_kpi?: ConcentrationKpi;
    judgment_accuracy?: JudgmentAccuracy;
    correlation_analysis?: CorrelationAnalysis;
    theme_strength?: ThemeStrength;
    factor_exposure?: FactorExposure;
  };
};

// 詳細パネルの判定表示（.magi-jverdict）への可否マッピング
const JV: Record<string, { word: string; cls: string }> = {
  buy: { word: "買い", cls: "buy" },
  warn: { word: "慎重", cls: "warn" },
  hold: { word: "中立", cls: "warn" },
  sell: { word: "売り", cls: "no" },
  na: { word: "判定不能", cls: "no" },
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
    fetch(`/data/snapshot.json?t=${Date.now()}`, { cache: "no-store" })
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
          // 保有を snapshot.holdings から動的描画（dashboard.html .hold 構造を踏襲）
          const list = document.querySelector<HTMLElement>(".holdings-list");
          if (list) {
            const esc = (s: unknown): string =>
              String(s ?? "").replace(
                /[&<>"]/g,
                (c) =>
                  (({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }) as Record<string, string>)[c],
              );
            const fmtJpy0 = (n: number | null | undefined) =>
              n == null ? "—" : "¥" + Math.round(n).toLocaleString("ja-JP");
            const stCls = (st?: string) =>
              st === "REVIEW" ? "status-bad" : st === "WARN" ? "status-warn" : "status-good";
            const stWord = (st?: string) =>
              st === "REVIEW" ? "要確認" : st === "WARN" ? "注視" : "順調";
            const cards = holdingTickers.map((ticker) => {
              const h = snap.holdings[ticker];
              const dir = h.pnl?.direction === "down" ? "down" : "up";
              const color = dir === "down" ? "#f87171" : "#4ade80";
              const hist = h.history_30d ?? [];
              const cost = h.cost_price || hist[0] || 1;
              let graphInner: string;
              if (hist.length >= 2) {
                const n = hist.length;
                graphInner =
                  `<polyline fill="none" stroke="${color}" stroke-width="2.2" points="` +
                  hist
                    .map((v, i) => {
                      const x = 30 + (340 * i) / (n - 1);
                      const y = Math.max(6, Math.min(86, 60 - (v / cost - 1) * 100 * 2.5));
                      return `${x.toFixed(1)},${y.toFixed(1)}`;
                    })
                    .join(" ") +
                  `"/>`;
              } else {
                graphInner = `<text x="190" y="52" fill="#6b6962" font-size="9" text-anchor="middle" font-family="JetBrains Mono">価格履歴なし</text>`;
              }
              const tgtY =
                h.target_pct != null ? 60 - h.target_pct * 100 * 2.5 : null;
              const tgtLine =
                tgtY != null
                  ? `<line x1="30" y1="${tgtY.toFixed(1)}" x2="370" y2="${tgtY.toFixed(1)}" stroke="#a09d92" stroke-width="0.6" stroke-dasharray="4,3" opacity="0.5"/><text x="368" y="${(tgtY - 3).toFixed(1)}" fill="#a09d92" font-size="7" text-anchor="end" font-family="JetBrains Mono">目標 +${Math.round((h.target_pct ?? 0) * 100)}%</text>`
                  : "";
              const vmeta = BADGE[h.reconciliation] ?? BADGE.single;
              const verifyBadge = `<span class="hold-label verify-badge ${vmeta.cls}" title="出典:${esc(h.source ?? "-")} / 時点:${esc(h.as_of ?? "-")} / 照合:${esc(h.reconciliation)}">${vmeta.text}</span>`;
              const hmeta = h.health ? HEALTH_BADGE[h.health.state] ?? HEALTH_BADGE.OK : null;
              const healthBadge = hmeta
                ? `<span class="hold-label health-badge ${hmeta.cls}" style="color:${hmeta.color};border-color:${hmeta.color}">${hmeta.text}</span>`
                : "";
              const termLabels = [
                h.strategy ? `<span class="hold-label term">${esc(h.strategy)}</span>` : "",
                h.market || h.sector
                  ? `<span class="hold-label term">${esc([h.market, h.sector].filter(Boolean).join(" "))}</span>`
                  : "",
              ].join("");
              return `
                <div class="hold ${stCls(h.health?.state)}">
                  <div class="hold-labels">
                    <span class="hold-label ${stCls(h.health?.state)}">${stWord(h.health?.state)}</span>
                    ${termLabels}${verifyBadge}${healthBadge}
                  </div>
                  <div class="hold-top">
                    <div class="hold-left">
                      <span class="hold-ticker">${esc(ticker)}</span>
                      <span class="hold-name">${esc(h.name ?? ticker)}</span>
                    </div>
                    <div class="hold-right">
                      <span class="hold-price">${esc(h.price_display ?? "—")}</span>
                      <span class="hold-pnl ${dir}">${esc(h.pnl?.ratio_display ?? "—")}</span>
                    </div>
                  </div>
                  <div class="hold-meta" style="display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11px;opacity:.85;margin:2px 0 6px;">
                    <span><b>${h.qty ?? 0}</b>株</span>
                    <span>取得 ${fmtJpy0(h.cost_price)}</span>
                    <span>含み <b style="color:${color}">${fmtJpy0(h.unrealized_jpy)}</b></span>
                  </div>
                  <svg class="hold-graph" viewBox="0 0 380 90" preserveAspectRatio="none" data-graph="${esc(ticker)}">
                    <line x1="0" y1="10" x2="380" y2="10" stroke="#3a3a48" stroke-width="0.4" stroke-dasharray="2,2"/>
                    <line x1="0" y1="35" x2="380" y2="35" stroke="#3a3a48" stroke-width="0.4" stroke-dasharray="2,2"/>
                    <line x1="0" y1="60" x2="380" y2="60" stroke="#6b6962" stroke-width="0.7"/>
                    <line x1="0" y1="75" x2="380" y2="75" stroke="#3a3a48" stroke-width="0.4" stroke-dasharray="2,2"/>
                    <text x="2" y="13" fill="#6b6962" font-size="7" font-family="JetBrains Mono">+20%</text>
                    <text x="2" y="63" fill="#6b6962" font-size="7" font-family="JetBrains Mono">0%</text>
                    ${tgtLine}
                    ${graphInner}
                  </svg>
                  <div class="hold-status-bar">
                    <span class="lbl">進捗</span>
                    <span class="val ${dir === "down" ? "warn" : "good"}">${h.buy_date ? esc(h.buy_date) + " 取得" : "取得日不明"} · 含み ${esc(h.pnl?.ratio_display ?? "—")}</span>
                  </div>
                </div>`;
            });
            list.innerHTML = cards.join("");
            // 同期状態ヘッダ（確定/未報告/最終同期）
            const wrap = list.closest(".holdings-wrap") ?? list.parentElement;
            const snapMeta = snap as unknown as {
              generated_at?: string;
              pending_decisions?: Record<string, unknown>;
            };
            const pend = Object.keys(snapMeta.pending_decisions ?? {}).length;
            const syncHtml = `<span>確定 <b>${holdingTickers.length}</b>件</span><span>未報告 <b style="color:${pend ? "#f5a85a" : "inherit"}">${pend}</b>件</span><span>最終同期 ${esc(snapMeta.generated_at ?? "—")}</span>`;
            if (wrap) {
              let hdr = wrap.querySelector<HTMLElement>(".holdings-sync");
              if (!hdr) {
                hdr = document.createElement("div");
                hdr.className = "holdings-sync";
                hdr.style.cssText =
                  "font-size:11px;opacity:.8;margin:0 0 8px;display:flex;gap:14px;flex-wrap:wrap;";
                wrap.insertBefore(hdr, list);
              }
              hdr.innerHTML = syncHtml;
            }
          }
        }

        // === 増額ゲート⑥（paper/live 別 + combined 参考）& 段階大規模化（paper） ===
        const gsMount = document.getElementById("gate-scaling-mount");
        if (gsMount) {
          const sd = snap as unknown as {
            gates?: Record<
              string,
              | {
                  passed: boolean;
                  n: number;
                  actionable: boolean;
                  criteria: {
                    name: string;
                    value: unknown;
                    threshold: string;
                    passed: boolean;
                  }[];
                }
              | { error: string }
            >;
            scaling?: {
              current_risk_budget_jpy?: number;
              target_ceiling_jpy?: number;
              ceiling_progress_pct?: number | null;
              ladder_jpy?: number[];
              injections?: { created_at: string | null }[];
            };
          };
          const escG = (s: unknown) =>
            String(s ?? "").replace(
              /[&<>"]/g,
              (c) =>
                (({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }) as Record<string, string>)[c],
            );
          const yen = (n: number | null | undefined) =>
            n == null ? "—" : "¥" + Math.round(n).toLocaleString("ja-JP");

          // 段階大規模化 widget（paper 専用）
          const sc = sd.scaling;
          let scalingHtml = "";
          if (sc) {
            const cur = sc.current_risk_budget_jpy ?? 0;
            const ceil = sc.target_ceiling_jpy ?? 1000000;
            const pct = sc.ceiling_progress_pct ?? (ceil ? Math.round((cur / ceil) * 1000) / 10 : 0);
            const ladder = sc.ladder_jpy ?? [100000, 300000, 600000, 1000000];
            const ladderMarks = ladder
              .map((m) => {
                const reached = cur >= m;
                const left = ceil ? (m / ceil) * 100 : 0;
                return `<div style="position:absolute;left:${left}%;transform:translateX(-50%);top:-2px;text-align:center;"><div style="width:2px;height:14px;background:${reached ? "#4ade80" : "rgba(255,255,255,.25)"};margin:0 auto;"></div><div style="font-size:9px;color:${reached ? "#4ade80" : "var(--ink-3,#8893a5)"};margin-top:2px;white-space:nowrap;">¥${(m / 10000).toFixed(0)}万</div></div>`;
              })
              .join("");
            const injTxt =
              sc.injections && sc.injections.length
                ? `資本注入 ${sc.injections.length}件・最新 ${escG(sc.injections[sc.injections.length - 1].created_at)}`
                : "資本注入履歴なし（phase-C 開始でここに記録）";
            scalingHtml = `
              <div class="gs-card" style="background:var(--bg-2,#131923);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:16px;">
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
                  <span style="font-size:13px;font-weight:700;">📈 段階大規模化 <span style="font-size:11px;color:var(--ink-3,#8893a5);font-weight:400;">paper・試験運用</span></span>
                  <span style="font-size:11px;color:var(--ink-3,#8893a5);">¥10万 → ¥100万</span>
                </div>
                <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:14px;">
                  <span style="font-size:22px;font-weight:800;">${yen(cur)}</span>
                  <span style="font-size:12px;color:var(--ink-3,#8893a5);">/ 上限 ${yen(ceil)}（${pct ?? 0}%）</span>
                </div>
                <div style="position:relative;margin:0 4px 22px;">
                  <div style="height:8px;background:rgba(255,255,255,.08);border-radius:4px;overflow:hidden;"><div style="height:100%;width:${Math.min(100, pct ?? 0)}%;background:linear-gradient(90deg,#4ade80,#16a085);border-radius:4px;"></div></div>
                  <div style="position:relative;height:0;">${ladderMarks}</div>
                </div>
                <div style="font-size:11px;color:var(--ink-3,#8893a5);">${injTxt}</div>
              </div>`;
          }

          // 増額ゲート⑥ panels（paper/live/combined）
          const gd = sd.gates ?? {};
          const gateCard = (key: string, label: string, sub: string) => {
            const g = gd[key];
            if (!g)
              return `<div class="gs-card" style="background:var(--bg-2,#131923);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;font-size:12px;color:var(--ink-3,#8893a5);">${label}：データなし</div>`;
            if ("error" in g)
              return `<div class="gs-card" style="background:var(--bg-2,#131923);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;font-size:12px;color:var(--ink-3,#8893a5);">${label}：評価不可</div>`;
            const head = g.actionable
              ? g.passed
                ? `<span style="color:#4ade80;">✅ 通過</span>`
                : `<span style="color:#f5a85a;">⛔ 未通過</span>`
              : `<span style="color:#8893a5;">📊 参考（増額不可）</span>`;
            const crit = g.criteria
              .map(
                (c) =>
                  `<div style="display:flex;gap:6px;font-size:11px;line-height:1.7;"><span style="color:${c.passed ? "#4ade80" : "#f87171"};width:12px;">${c.passed ? "✓" : "✗"}</span><span style="flex:1;color:var(--ink-2,#a8a89e);">${escG(c.name)}</span><span style="color:var(--ink-1,#e8ecf3);">${escG(c.value)}</span><span style="color:var(--ink-3,#8893a5);">(${escG(c.threshold)})</span></div>`,
              )
              .join("");
            const border = !g.actionable
              ? "rgba(136,147,165,.4)"
              : g.passed
                ? "rgba(74,222,128,.35)"
                : "rgba(245,168,90,.3)";
            return `
              <div class="gs-card" style="background:var(--bg-2,#131923);border:1px solid ${border};border-radius:12px;padding:14px;">
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px;">
                  <span style="font-size:13px;font-weight:700;">${label}</span>${head}
                </div>
                <div style="font-size:11px;color:var(--ink-3,#8893a5);margin-bottom:8px;">${sub}・公式評価 n=${g.n}</div>
                ${crit || `<div style="font-size:11px;color:var(--ink-3,#8893a5);">評価対象なし（約定→評価が貯まると判定開始）</div>`}
              </div>`;
          };

          gsMount.innerHTML = `
            <div class="section-label" style="margin-top:24px;"><span>増額ゲート⑥ & 運用規模</span><span class="zone-tag tag-magi" style="margin-left:8px">KATSURAGI</span></div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px;">
              ${gateCard("paper", "ゲート⑥ paper", "システム edge 検証（DS自動fill）")}
              ${gateCard("live", "ゲート⑥ live", "実運用（楽天・手動）")}
              ${gateCard("combined", "参考 combined", "paper+live 混在・増額不可")}
            </div>
            ${scalingHtml}`;
        }

        // === Phase C レポートビュー（#phase-c-body に5ブロック注入・コスト0データ） ===
        const pcBody = document.getElementById("phase-c-body");
        const pc = (snap as unknown as {
          phase_c?: {
            error?: string;
            intent?: {
              treasury?: Record<string, Record<string, number | string | null>>;
              auto_trade?: { master_on?: boolean; pilots_on?: string[] };
              halt?: { on?: boolean; reason?: string };
            };
            pnl_realized?: Record<string, { n: number; pnl_jpy: number; wins: number }>;
            gates?: Record<
              string,
              {
                passed: boolean;
                n: number;
                actionable: boolean;
                criteria: { name: string; value: string; threshold: string; passed: boolean }[];
              }
            >;
            pilot_performance_paper?: Record<string, { n: number; hit_rate: number; avg_r: number }>;
            fix_direction_paper?: {
              failing_criteria?: { name: string; value: string; threshold: string }[];
              promotions?: { personality: string; note: string }[];
            };
            data_breakdown?: {
              total?: number;
              evaluated?: number;
              by_status?: Record<string, number>;
              by_filled_via?: Record<string, number>;
              by_broker_mode?: Record<string, number>;
            };
            purchase_history_paper?: {
              purchases?: {
                ticker: string;
                name: string;
                buy_date: string | null;
                buy_price: number;
                qty: number;
                cost_jpy: number;
                status: string;
                sell_price: number | null;
                closed_reason: string | null;
                realized_pnl_jpy: number | null;
              }[];
              official_n?: number;
              legacy_excluded_n?: number;
            };
          };
        }).phase_c;
        if (pcBody && pc && !pc.error) {
          const e2 = (s: unknown) =>
            String(s ?? "").replace(
              /[&<>"]/g,
              (c) => (({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }) as Record<string, string>)[c],
            );
          const y2 = (n: number | null | undefined) =>
            n == null ? "—" : "¥" + Math.round(Number(n)).toLocaleString("ja-JP");
          const num = (n: unknown) => Number(n ?? 0);
          const sect = (label: string) =>
            `<div style="font-size:13px;font-weight:800;color:#a78bfa;margin:22px 0 10px;letter-spacing:.04em;">${label}</div>`;
          const card = (inner: string) =>
            `<div style="background:#131923;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:16px;margin-bottom:12px;">${inner}</div>`;

          // ① 実行意図 + paper/live 解放枠ウィジェット
          const tre = pc.intent?.treasury ?? {};
          const unlockWidget = (
            t: Record<string, number | string | null> | undefined,
            label: string,
            accent: string,
          ) => {
            if (!t) return "";
            const cap = num(t.account_capital_jpy);
            const unlocked = num(t.current_risk_budget_jpy);
            const exposure = num(t.active_exposure_jpy);
            const deployable = num(t.deployable_jpy);
            const prog = num(t.ceiling_progress_pct);
            const ceil = num(t.target_ceiling_jpy);
            const unlockedOfCap = cap ? Math.min(100, (unlocked / cap) * 100) : 0;
            const expOfUnlocked = unlocked ? Math.min(100, (exposure / unlocked) * 100) : 0;
            const stat = (lbl: string, val: string, c: string) =>
              `<div style="flex:1;min-width:110px;"><div style="font-size:10px;color:#8893a5;text-transform:uppercase;letter-spacing:.05em;">${lbl}</div><div style="font-size:16px;font-weight:800;color:${c};">${val}</div></div>`;
            return card(`
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;"><span style="font-size:13px;font-weight:700;">${label}</span><span style="font-size:11px;color:#8893a5;">上限 ${y2(ceil)} / 解放 ${prog}%</span></div>
              <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
                ${stat("口座総額", y2(cap), "#e8ecf3")}
                ${stat("解放枠 unlocked", y2(unlocked), accent)}
                ${stat("使用中 exposure", y2(exposure), "#f5a85a")}
                ${stat("deploy 可能", y2(deployable), "#4ade80")}
              </div>
              <div style="font-size:10px;color:#8893a5;margin-bottom:3px;">口座に対する解放枠（${unlockedOfCap.toFixed(0)}%）</div>
              <div style="height:7px;background:rgba(255,255,255,.07);border-radius:4px;overflow:hidden;margin-bottom:10px;"><div style="height:100%;width:${unlockedOfCap}%;background:${accent};border-radius:4px;"></div></div>
              <div style="font-size:10px;color:#8893a5;margin-bottom:3px;">解放枠の内訳：使用中 ${expOfUnlocked.toFixed(0)}% / 空き</div>
              <div style="height:7px;background:#16331f;border-radius:4px;overflow:hidden;"><div style="height:100%;width:${expOfUnlocked}%;background:#f5a85a;"></div></div>
            `);
          };
          const at = pc.intent?.auto_trade;
          const ha = pc.intent?.halt;
          const intentExtra = card(`
            <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px;">
              <div><span style="color:#8893a5;">自動売買</span> <b style="color:${at?.master_on ? "#4ade80" : "#8893a5"};">${at?.master_on ? "🟢 master ON" : "⚫ master OFF"}</b> ${at?.pilots_on?.length ? "／稼働 " + at.pilots_on.map(e2).join(",") : "／稼働なし"}</div>
              <div><span style="color:#8893a5;">HALT</span> <b style="color:${ha?.on ? "#f87171" : "#4ade80"};">${ha?.on ? "⛔ ON " + e2(ha.reason) : "🟢 なし"}</b></div>
            </div>`);
          const block1 =
            sect("① 実行意図（いま何を回しているか）") +
            unlockWidget(tre.paper, "paper（試験運用・DS自動fill）", "#a78bfa") +
            unlockWidget(tre.live, "live（楽天本番・手動）", "#4ec9b0") +
            intentExtra;

          // ② 損益（確定・broker_mode別）
          const pnl = pc.pnl_realized ?? {};
          const pnlRows = Object.entries(pnl)
            .filter(([, d]) => d.n > 0)
            .map(([mode, d]) => {
              const wr = d.n ? (d.wins / d.n) * 100 : 0;
              const sign = d.pnl_jpy >= 0 ? "+" : "";
              return `<div style="display:flex;justify-content:space-between;font-size:12px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05);"><span><b>${e2(mode)}</b> 確定 ${d.n}件・勝ち ${d.wins}（${wr.toFixed(0)}%）</span><span style="font-weight:700;color:${d.pnl_jpy >= 0 ? "#4ade80" : "#f87171"};">${sign}${y2(d.pnl_jpy)}</span></div>`;
            })
            .join("");
          const block2 =
            sect("② 損益（確定・closed・broker_mode別／含み損益はダッシュボード）") +
            card(pnlRows || `<div style="font-size:12px;color:#8893a5;">確定取引まだなし</div>`);

          // ③ 分析（gate paper/live/combined + 機体別実績）
          const gd = pc.gates ?? {};
          const gcard = (key: string, label: string) => {
            const g = gd[key];
            if (!g) return "";
            const head = g.actionable
              ? g.passed
                ? `<span style="color:#4ade80;">✅ 通過</span>`
                : `<span style="color:#f5a85a;">⛔ 未通過</span>`
              : `<span style="color:#8893a5;">📊 参考・増額不可</span>`;
            const crit = (g.criteria ?? [])
              .map(
                (c) =>
                  `<div style="display:flex;gap:6px;font-size:11px;line-height:1.7;"><span style="color:${c.passed ? "#4ade80" : "#f87171"};width:12px;">${c.passed ? "✓" : "✗"}</span><span style="flex:1;color:#a8a89e;">${e2(c.name)}</span><span style="color:#e8ecf3;">${e2(c.value)}</span><span style="color:#8893a5;">(${e2(c.threshold)})</span></div>`,
              )
              .join("");
            const bd = !g.actionable
              ? "rgba(136,147,165,.4)"
              : g.passed
                ? "rgba(74,222,128,.35)"
                : "rgba(245,168,90,.3)";
            return `<div style="flex:1;min-width:240px;background:#0f141d;border:1px solid ${bd};border-radius:10px;padding:12px;"><div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:12px;font-weight:700;"><span>${label}</span>${head}</div><div style="font-size:10px;color:#8893a5;margin-bottom:6px;">公式評価 n=${g.n}</div>${crit}</div>`;
          };
          const perf = pc.pilot_performance_paper ?? {};
          const perfRows = Object.entries(perf)
            .map(
              ([name, d]) =>
                `<div style="display:flex;justify-content:space-between;font-size:11px;padding:4px 0;"><span>${e2(name)}</span><span style="color:#8893a5;">n=${d.n}・命中 ${(d.hit_rate * 100).toFixed(0)}%・平均R ${d.avg_r >= 0 ? "+" : ""}${d.avg_r.toFixed(2)}</span></div>`,
            )
            .join("");
          const block3 =
            sect("③ 分析（増額ゲート⑥ paper/live 別 + 機体別実績）") +
            card(
              `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">${gcard("paper", "ゲート⑥ paper")}${gcard("live", "ゲート⑥ live")}${gcard("combined_reference", "参考 combined")}</div><div style="font-size:11px;color:#8893a5;margin-bottom:4px;">機体別 実績（paper）</div>${perfRows || `<div style="font-size:11px;color:#8893a5;">まだ評価データなし</div>`}`,
            );

          // ④ 修正の方向性（次にやること）
          const fix = pc.fix_direction_paper ?? {};
          const failing = fix.failing_criteria ?? [];
          const failHtml = failing.length
            ? failing
                .map(
                  (c) =>
                    `<div style="display:flex;gap:8px;font-size:12px;padding:6px 0;border-bottom:1px solid rgba(245,168,90,.12);"><span style="color:#f5a85a;">✗</span><span style="flex:1;"><b>${e2(c.name)}</b></span><span style="color:#8893a5;">現在 ${e2(c.value)} → 要件 ${e2(c.threshold)}</span></div>`,
                )
                .join("")
            : `<div style="font-size:12px;color:#4ade80;">paper ゲート⑥ 全要件 達成 → 増額余地あり</div>`;
          const proms = fix.promotions ?? [];
          const promHtml = proms.length
            ? `<div style="font-size:11px;color:#8893a5;margin-top:10px;">昇格候補：</div>` +
              proms
                .map((p) => `<div style="font-size:11px;">${e2(p.personality)}：${e2(p.note)}</div>`)
                .join("")
            : "";
          const block4 =
            sect("④ 修正の方向性（次にやること）") +
            card(
              `<div style="background:rgba(245,168,90,.08);border:1px solid rgba(245,168,90,.25);border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:700;color:#f5a85a;margin-bottom:6px;">⚑ 次に直す＝増額ゲート⑥ 未達要件</div>${failHtml}</div>${promHtml}`,
            );

          // ⑤ 要素データ
          const db = pc.data_breakdown ?? {};
          const dictRow = (lbl: string, obj: Record<string, number> | undefined) =>
            `<div style="font-size:11px;padding:4px 0;"><span style="color:#8893a5;">${lbl}</span> ${Object.entries(obj ?? {})
              .map(([k, v]) => `${e2(k)}=${v}`)
              .join(" / ") || "—"}</div>`;
          const block5 =
            sect("⑤ 要素データ（母集団の内訳）") +
            card(
              `<div style="font-size:12px;margin-bottom:6px;">decisions 総数 <b>${num(db.total)}</b> / 評価済 <b>${num(db.evaluated)}</b></div>${dictRow("status:", db.by_status)}${dictRow("filled_via:", db.by_filled_via)}${dictRow("broker_mode:", db.by_broker_mode)}<div style="font-size:10px;color:#8893a5;margin-top:8px;">※ 公式集合 = filled_via∈(ds_dispatch,manual) ∧ entry_market_regime有 ∧ broker_mode一致</div>`,
            );

          // ⑥ 約定履歴（何を・いつ・いくらで・何株 買ったか）= トレード台帳
          const ph = pc.purchase_history_paper;
          const phList = ph?.purchases ?? [];
          const phRows = phList
            .map((p) => {
              const stCell =
                p.status === "closed" && p.realized_pnl_jpy != null
                  ? `<span style="color:${p.realized_pnl_jpy >= 0 ? "#4ade80" : "#f87171"};font-weight:700;">${p.realized_pnl_jpy >= 0 ? "+" : ""}${Math.round(p.realized_pnl_jpy).toLocaleString("ja-JP")}円</span><span style="color:#8893a5;font-size:10px;"> 売${e2(p.sell_price)}（${e2(p.closed_reason)}）</span>`
                  : `<span style="color:#8893a5;">保有中</span>`;
              return `<tr style="border-top:1px solid rgba(255,255,255,.06);">
                <td style="padding:6px 8px;color:#c7d0dc;white-space:nowrap;">${e2(p.buy_date)}</td>
                <td style="padding:6px 8px;"><b style="color:#e6ebf2;">${e2(p.ticker)}</b> <span style="color:#8893a5;font-size:10px;">${e2(p.name)}</span></td>
                <td style="padding:6px 8px;text-align:right;color:#c7d0dc;">${num(p.qty)}株</td>
                <td style="padding:6px 8px;text-align:right;color:#c7d0dc;">@${num(p.buy_price).toLocaleString("ja-JP")}</td>
                <td style="padding:6px 8px;text-align:right;color:#c7d0dc;">${y2(p.cost_jpy)}</td>
                <td style="padding:6px 8px;text-align:right;white-space:nowrap;">${stCell}</td>
              </tr>`;
            })
            .join("");
          const block6 =
            sect(`⑥ 約定履歴（何を・いつ・いくらで · official ${num(ph?.official_n)}件）`) +
            card(
              phList.length
                ? `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;">
                     <thead><tr style="color:#8893a5;font-size:10px;text-align:left;">
                       <th style="padding:4px 8px;">買付日</th><th style="padding:4px 8px;">銘柄</th>
                       <th style="padding:4px 8px;text-align:right;">株数</th><th style="padding:4px 8px;text-align:right;">買値</th>
                       <th style="padding:4px 8px;text-align:right;">取得原価</th><th style="padding:4px 8px;text-align:right;">状態 / 実現損益</th>
                     </tr></thead><tbody>${phRows}</tbody></table></div>
                     <div style="font-size:10px;color:#8893a5;margin-top:8px;">※ official(ds_dispatch/manual) のみ。legacy ${num(ph?.legacy_excluded_n)}件は別掲で除外。含み損益は保有カード側。</div>`
                : `<div style="font-size:12px;color:#8893a5;">約定なし（買い候補から発注・紙約定が入るとここに台帳が並ぶ）</div>`,
            );

          pcBody.innerHTML = block1 + block2 + block3 + block4 + block5 + block6;
        }

        // === 集中投資 KPI バインディング (v2.10) ===
        // dashboard.html の <span data-kpi="seed_jpy" data-kpi-fmt="jpy"> 等に
        // snapshot.dummy_system.concentration_kpi の値を流し込む。
        const kpi = snap.dummy_system?.concentration_kpi;
        if (kpi) {
          document.querySelectorAll<HTMLElement>("[data-kpi]").forEach((el) => {
            const key = el.dataset.kpi as keyof ConcentrationKpi | undefined;
            if (!key || !(key in kpi)) return;
            const raw = kpi[key];
            if (typeof raw !== "number") return;
            const fmt = el.dataset.kpiFmt;
            if (fmt === "jpy") {
              el.textContent = `¥${Math.round(raw).toLocaleString()}`;
            } else if (fmt === "jpy-signed") {
              const sign = raw >= 0 ? "+" : "−";
              el.textContent = `${sign}¥${Math.abs(Math.round(raw)).toLocaleString()}`;
              const wrap = el.closest<HTMLElement>(".track-stat-value");
              if (wrap) {
                wrap.classList.remove("up", "down");
                wrap.classList.add(raw >= 0 ? "up" : "down");
              }
            } else if (fmt === "pct") {
              el.textContent = raw.toFixed(1);
            } else if (fmt === "pct-signed") {
              const sign = raw >= 0 ? "+" : "−";
              el.textContent = `${sign}${Math.abs(raw).toFixed(2)}`;
            } else {
              // position_count 等の整数
              el.textContent = String(Math.round(raw));
            }
          });
        }

        // === 判断精度バインディング (v2.10 Phase 2 Mini) ===
        // Track Record カードに snapshot.dummy_system.judgment_accuracy を反映
        const ja = snap.dummy_system?.judgment_accuracy;
        if (ja) {
          const ov = ja.overall;
          const setText = (sel: string, txt: string) => {
            const el = document.querySelector<HTMLElement>(sel);
            if (el) el.textContent = txt;
          };
          // card-meta（過去 30 日 · 状態）
          const metaTxt =
            ja.status === "insufficient_data"
              ? `過去${ja.lookback_days}日 · データ蓄積中 (評価済 ${ov.evaluated}/${ja.min_samples_required} 必要)`
              : ja.status === "error"
                ? `過去${ja.lookback_days}日 · 集計エラー`
                : `過去${ja.lookback_days}日`;
          setText("[data-ja-meta]", metaTxt);

          // 的中率
          const accEl = document.querySelector<HTMLElement>(
            "[data-ja='accuracy']",
          );
          if (accEl) {
            accEl.classList.remove("up", "down");
            if (ov.accuracy_pct === null) {
              accEl.textContent = "蓄積中";
            } else {
              accEl.textContent = `${ov.accuracy_pct.toFixed(0)}%`;
              accEl.classList.add(ov.accuracy_pct >= 50 ? "up" : "down");
            }
          }
          setText(
            "[data-ja='evaluated_summary']",
            `${ov.hits} / ${ov.evaluated} 件 (待ち ${ov.pending})`,
          );

          // 平均リターン
          const arEl = document.querySelector<HTMLElement>(
            "[data-ja='avg_return']",
          );
          if (arEl) {
            arEl.classList.remove("up", "down");
            if (ov.avg_return_pct === null) {
              arEl.textContent = "—";
            } else {
              const sign = ov.avg_return_pct >= 0 ? "+" : "";
              arEl.textContent = `${sign}${ov.avg_return_pct.toFixed(1)}%`;
              arEl.classList.add(ov.avg_return_pct >= 0 ? "up" : "down");
            }
          }
        }

        // === Phase 1A-Step2: ピラミッディング状況バインディング ===
        // dummy_system.personalities[].holdings[] から pyramid_stage を集めて
        // 「機別の段階」を一覧表示する。
        type HoldingExt = {
          ticker: string;
          name?: string;
          pyramid_stage?: string;
          current_alloc_pct?: number | null;
          peak_pnl_pct?: number | null;
          unrealized_pct?: number;
        };
        type PersonalitySnap = {
          name: string;
          icon?: string;
          holdings?: HoldingExt[];
        };
        const ds = (snap as unknown as {
          dummy_system?: { personalities?: PersonalitySnap[] };
        }).dummy_system;
        const pyramidStatsEl = document.querySelector<HTMLElement>(
          "#pyramid-status-stats",
        );
        const pyramidMetaEl = document.querySelector<HTMLElement>("[data-pyr-meta]");
        if (pyramidStatsEl && ds?.personalities) {
          const allHoldings: { pilot: string; icon: string; h: HoldingExt }[] = [];
          for (const p of ds.personalities) {
            for (const h of p.holdings ?? []) {
              allHoldings.push({ pilot: p.name, icon: p.icon ?? "", h });
            }
          }
          if (allHoldings.length === 0) {
            pyramidStatsEl.innerHTML = "";
            if (pyramidMetaEl) pyramidMetaEl.textContent = "保有銘柄なし";
          } else {
            const html = allHoldings
              .map(({ pilot, icon, h }) => {
                const stage = h.pyramid_stage ?? "—";
                const alloc = h.current_alloc_pct != null
                  ? `${h.current_alloc_pct.toFixed(0)}%`
                  : "—";
                const peak = h.peak_pnl_pct != null
                  ? `peak ${h.peak_pnl_pct >= 0 ? "+" : ""}${h.peak_pnl_pct.toFixed(1)}%`
                  : "peak n/a";
                return `
                  <div class="track-stat">
                    <div class="track-stat-label">${icon}${pilot} · ${h.ticker}</div>
                    <div class="track-stat-value">${stage}</div>
                    <div class="track-stat-sub">${alloc} / ${peak}</div>
                  </div>
                `;
              })
              .join("");
            pyramidStatsEl.innerHTML = html;
            if (pyramidMetaEl)
              pyramidMetaEl.textContent = `${allHoldings.length} 銘柄`;
          }
        }

        // === Phase 1: ポートフォリオ相関分析バインディング ===
        const corr = snap.dummy_system?.correlation_analysis;
        if (corr) {
          const setText = (sel: string, txt: string) => {
            const el = document.querySelector<HTMLElement>(sel);
            if (el) el.textContent = txt;
          };
          if (corr.status === "active") {
            setText(
              "[data-corr='concentration_label']",
              corr.concentration_label ?? "—",
            );
            setText(
              "[data-corr='max_corr']",
              corr.max_corr != null ? corr.max_corr.toFixed(3) : "—",
            );
            setText(
              "[data-corr='mean_abs_corr']",
              corr.mean_abs_corr != null ? corr.mean_abs_corr.toFixed(3) : "—",
            );
            setText(
              "[data-corr='high_pairs_count']",
              String(corr.high_correlation_pairs?.length ?? 0),
            );
            setText("[data-corr-meta]", `30 日 / ${corr.tickers?.length ?? 0} 銘柄`);
          } else {
            setText("[data-corr='concentration_label']", "蓄積中");
            setText("[data-corr-meta]", "保有 2 件以上で計算");
          }
        }

        // === Phase 4: テーマ強度バインディング ===
        const ts = snap.dummy_system?.theme_strength;
        if (ts) {
          const setText = (sel: string, txt: string) => {
            const el = document.querySelector<HTMLElement>(sel);
            if (el) el.textContent = txt;
          };
          const top = ts.top_themes ?? [];
          for (let i = 0; i < 3; i++) {
            const name = top[i];
            const m = name ? ts.themes?.[name] : null;
            setText(`[data-theme='top${i + 1}_name']`, name ?? "—");
            if (m) {
              const mom = m.momentum != null ? `mom ${m.momentum >= 0 ? "+" : ""}${m.momentum.toFixed(2)}` : "";
              setText(
                `[data-theme='top${i + 1}_metrics']`,
                `i=${m.intensity} / 7d=${m.mentions_7d} / 30d=${m.mentions_30d} ${mom}`,
              );
            } else {
              setText(`[data-theme='top${i + 1}_metrics']`, "—");
            }
          }
          setText("[data-theme='total_topics']", String(ts.total_topics_30d ?? 0));
          if (ts.status !== "active") {
            setText("[data-theme-meta]", ts.status === "no_matches" ? "ヒットなし" : "蓄積中");
          } else {
            setText("[data-theme-meta]", "過去 30 日 / Top 3");
          }
        }

        // === Phase 5: factor exposure バインディング ===
        const fx = snap.dummy_system?.factor_exposure;
        if (fx) {
          const setText = (sel: string, txt: string) => {
            const el = document.querySelector<HTMLElement>(sel);
            if (el) el.textContent = txt;
          };
          if (fx.status === "active") {
            setText(
              "[data-fx='concentration_label']",
              fx.concentration_label ?? "—",
            );
            setText("[data-fx='max_factor']", fx.max_factor ?? "—");
            const wf = fx.weighted_factors;
            const fmt = (v: number | null | undefined) =>
              v != null ? v.toFixed(1) : "—";
            setText("[data-fx='momentum']", fmt(wf?.momentum));
            setText("[data-fx='value']", fmt(wf?.value));
            setText("[data-fx='size']", fmt(wf?.size));
            setText("[data-fx-meta]", "4 軸の加重平均");
          } else {
            setText("[data-fx='concentration_label']", "蓄積中");
            setText("[data-fx-meta]", "保有あり + データ整備が必要");
          }
        }

        // === MAGI 買い候補に推奨サイジングを注入 ===
        // snapshot.candidates.<id>.sizing を読み、buy-zone の対応 .act に
        // "推奨 ¥X (Y株・PF比Z%・note)" 行を差し込む。
        const cands = snap.candidates ?? {};
        Object.entries(cands).forEach(([id, cand]) => {
          if (!cand?.sizing) return;
          const card = document.querySelector(
            `.buy-zone .act[data-detail="${id}"]`,
          );
          if (!card || card.querySelector(".magi-sizing")) return;
          const meta = card.querySelector(".forecast .forecast-meta");
          if (!meta) return;
          const sz = cand.sizing;
          const sizingDiv = document.createElement("div");
          sizingDiv.className = "magi-sizing";
          sizingDiv.innerHTML = `
            <span class="ms-label">推奨</span>
            <span class="ms-jpy">${sz.amount_display}</span>
            <span class="ms-detail">(${sz.shares}株・PF比 ${sz.weight_pct}%・${sz.note})</span>
          `;
          meta.parentNode?.insertBefore(sizingDiv, meta.nextSibling);
        });

        // === 保有 ↔ 売り注視の連動視覚化 ===
        // 売り Zone（推奨＋mini-act 注視）から ticker を集め、対応する保有カードに
        // MAGI 推奨バッジを足す。保有 = 守られているのか／売り推奨が来ているかを
        // 一目で見えるようにする。
        const sellMap = new Map<string, string>();
        document
          .querySelectorAll<HTMLElement>(".sell-zone .act")
          .forEach((act) => {
            const ticker = act
              .querySelector(".act-ticker")
              ?.textContent?.trim();
            const word =
              act.querySelector(".g-word")?.textContent?.trim() ?? "売り";
            if (ticker) sellMap.set(ticker, word);
          });
        document
          .querySelectorAll<HTMLElement>(".sell-zone .mini-act")
          .forEach((m) => {
            const ticker = m
              .querySelector(".mini-act-ticker")
              ?.textContent?.trim();
            if (ticker && !sellMap.has(ticker)) sellMap.set(ticker, "注視");
          });
        document.querySelectorAll<HTMLElement>(".hold").forEach((card) => {
          const ticker = card
            .querySelector(".hold-ticker")
            ?.textContent?.trim();
          if (!ticker || !sellMap.has(ticker)) return;
          const action = sellMap.get(ticker)!;
          const labels = card.querySelector(".hold-labels");
          if (labels && !labels.querySelector(".sell-watch-badge")) {
            const badge = document.createElement("span");
            badge.className = "hold-label sell-watch-badge";
            badge.textContent = `⚠ MAGI ${action}`;
            badge.title = `売り Zone と連動：MAGI が ${action} を推奨中。詳細パネルで根拠を確認`;
            labels.appendChild(badge);
          }
        });

        // 口座サマリ（現金・総資産）を反映
        if (snap.account) {
          const eq = document.querySelector(".sb-equity-value");
          if (eq) eq.textContent = `¥${snap.account.total_assets.toLocaleString()}`;
          const today = document.querySelector<HTMLElement>(".sb-equity-today");
          if (today) {
            // 全機の pnl_jpy 合計を表示
            const dsAny = (snap as unknown as {
              dummy_system?: { personalities?: { pnl_jpy?: number }[] };
            }).dummy_system;
            const pnl = (dsAny?.personalities ?? []).reduce(
              (acc, p) => acc + (p.pnl_jpy ?? 0),
              0,
            );
            const sign = pnl >= 0 ? "+" : "";
            today.textContent = `${sign}¥${Math.round(pnl).toLocaleString()} 含み損益`;
            today.classList.remove("up", "down");
            if (pnl > 0) today.classList.add("up");
            if (pnl < 0) today.classList.add("down");
          }
        }
        // v2.10: broker_mode + broker_provider を反映（サイドバーのバッジ）
        const brokerBadge = document.getElementById("sb-broker-badge");
        if (brokerBadge) {
          const mode = (snap as unknown as { dummy_system?: { broker_mode?: string } })
            .dummy_system?.broker_mode;
          const provider = (snap as unknown as { broker_provider?: string }).broker_provider;
          // provider 別バッジ
          const providerIcon: Record<string, string> = {
            rakuten: "🟢 楽天",
            sbi: "🔵 SBI",
            monex: "🟡 マネックス",
            kabucom: "🟠 kabu.com",
            moomoo: "🟣 moomoo",
            fractional: "⚪ Sim",
          };
          const providerLabel = provider && providerIcon[provider] ? providerIcon[provider] : "🧪";
          if (mode === "live" || mode === "moomoo_live") {
            brokerBadge.textContent = `⚡ ${providerLabel} LIVE`;
            brokerBadge.className = "sb-broker-badge live";
          } else {
            brokerBadge.textContent = `${providerLabel} Paper`;
            brokerBadge.className = "sb-broker-badge paper";
          }
        }

        // 接続インジケータ（.sb-live）：保有ソースを正直に表示
        // v2.10: 楽天/SBI/kabu.com も実接続扱いとして classify
        const live = document.querySelector(".sb-live");
        if (live) {
          const realPrefixes = ["moomoo", "楽天", "SBI", "マネックス", "auカブコム", "kabu"];
          const isReal = realPrefixes.some(p => snap.holdings_source.startsWith(p));
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
          if (state) {
            state.textContent = cand.split;
            if (cand.split_interp)
              (state as HTMLElement).title = cand.split_interp; // 割れ方の解釈
          }

          // 碇司令（B5）：推奨＋反対論拠を候補カードに表示
          if (cand.commander) {
            let cmdEl = card.querySelector<HTMLElement>(".cmd-line");
            if (!cmdEl && mini.parentElement) {
              cmdEl = document.createElement("div");
              cmdEl.className = "cmd-line";
              mini.parentElement.appendChild(cmdEl);
            }
            if (cmdEl) {
              cmdEl.textContent = `碇：${cand.commander.recommendation}`;
              cmdEl.title = `${cand.commander.counter}\n${cand.commander.src_note}`;
            }
          }

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

          // 詳細パネル（決裁画面）にも反映。役割名で行を対応づけ、要素が無ければスキップ
          const panel = document.querySelector(`[data-panel="${cardId}"]`);
          if (panel) {
            const byRole: Record<string, JudgeMini> = {};
            cand.judges.forEach((j) => (byRole[j.role] = j));
            panel.querySelectorAll(".magi-jrow").forEach((row) => {
              const name = row.querySelector(".magi-jname")?.textContent?.trim();
              const j = name ? byRole[name] : undefined;
              if (!j) return;
              const m = JV[j.verdict] ?? JV.na;
              const vd = row.querySelector(".magi-jverdict");
              if (vd) {
                vd.textContent = m.word;
                vd.className = `magi-jverdict ${m.cls}`;
              }
              const reason = row.querySelector(".magi-jreason");
              if (reason) {
                // 反証（自領域の逆向き事実）があれば根拠文に併記（要素は増やさない）
                const counter = j.counter && j.counter.length
                  ? `　／反証：${j.counter.join("・")}`
                  : "";
                reason.textContent = `${j.reason}${counter}`;
              }
            });
            const mfState = panel.querySelector(".mf-state");
            if (mfState) mfState.textContent = cand.split;
            if (cand.commander) {
              const rec = panel.querySelector(".cmd-rec");
              if (rec) rec.textContent = cand.commander.recommendation;
              const cnt = panel.querySelector(".cmd-counter");
              if (cnt)
                cnt.innerHTML = `<b>反対するなら：</b>${cand.commander.counter.replace(/^反対するなら：/, "")}`;
              const src = panel.querySelector(".cmd-src");
              if (src) src.textContent = cand.commander.src_note;
            }
            // GENDO推奨カード：碇ゾーンを初心者向けの「推奨アクション＋ガードレール＋確信度」に格上げ
            if (cand.gendo_card) {
              const gc = cand.gendo_card;
              const sleeve = gc.sleeve !== "—" ? `（${gc.sleeve}）` : "";
              const rec = panel.querySelector(".cmd-rec");
              if (rec) rec.textContent = `GENDO推奨：${gc.action}${sleeve}　${gc.reason}`;
              const src = panel.querySelector(".cmd-src");
              if (src)
                src.textContent =
                  `従うなら：${gc.guardrail}　｜　確信度 守り${gc.defense_confidence}` +
                  `／攻め${gc.offense_confidence}　｜　${gc.learn_note}`;
            }
            if (cand.verification) {
              const flagEls = panel.querySelectorAll(".magi-flag");
              cand.verification.flags.forEach((f, i) => {
                const el = flagEls[i];
                if (el)
                  el.textContent = `${f.label} ${f.status === "ok" ? "✓" : "⚠"}`;
              });
            }
          }
        });

        // === モック銘柄／統計の全面クリーン化（ローカル UI は実データだけを見せる）===
        // dashboard.html は F0 ゴールデンマスター（デザインリファレンス）として
        // サンプル銘柄が焼き込まれている。実運用 UI では snapshot に対応データが
        // 無いカード／統計を非表示・ゼロ化して、ユーザーがモックを実データと誤認しないようにする。
        const isHoldingsEmpty = holdingTickers.length === 0;
        const sellSection =
          ((snap as unknown) as { sell?: Record<string, unknown> }).sell ?? {};
        const sellIds = new Set(Object.keys(sellSection));
        const allocation =
          ((snap as unknown) as {
            allocation?: {
              current: { core: number; satellite: number; cash: number };
            };
          }).allocation;
        const totalAssets = snap.account?.total_assets ?? 0;

        // --- サイドバー .alloc-list を allocation.current で上書き ---
        if (allocation) {
          const allocList = document.querySelector(".alloc-list");
          if (allocList) {
            const tiers: { label: string; color: string; value: number }[] = [
              { label: "Core（ETF・高配当）", color: "#16a085", value: allocation.current.core },
              { label: "Satellite（個別株）", color: "#ff8c42", value: allocation.current.satellite },
              { label: "現金（防御余力）", color: "#888888", value: allocation.current.cash },
            ];
            allocList.innerHTML = tiers
              .map((t) => {
                const pct = totalAssets > 0 ? Math.round((t.value / totalAssets) * 100) : 0;
                const k = (t.value / 1000).toFixed(1);
                return `
                  <div class="alloc-row">
                    <div class="alloc-head">
                      <span class="alloc-name">
                        <span class="alloc-dot" style="background:${t.color}"></span>
                        ${t.label}
                      </span>
                      <span>
                        <span class="alloc-amount">¥${k}k</span>
                        <span class="alloc-pct">${pct}%</span>
                      </span>
                    </div>
                    <div class="alloc-bar"><div class="alloc-bar-fill" style="width:${pct}%; background:${t.color}"></div></div>
                  </div>
                `;
              })
              .join("");
          }
          // 配分セクションの見出し ¥108k → 実 total
          document.querySelectorAll<HTMLElement>(".sb-section-title .sub").forEach((el) => {
            if (el.textContent?.includes("¥")) {
              el.textContent = `¥${Math.round(totalAssets / 1000)}k`;
            }
          });
        }

        // --- サイドバー「今日のサマリー」を実データに（売り/買い/警告/的中率） ---
        const todayItems = document.querySelectorAll<HTMLElement>(".sb-today-list .sb-today-item");
        const candIdsForSummary = Object.keys(snap.candidates ?? {});
        const summaryValues: { sellCount: number; buyCount: number; warnCount: number; hitRate: string } = {
          sellCount: isHoldingsEmpty ? 0 : Object.keys(sellSection).length,
          buyCount: candIdsForSummary.length,
          warnCount: 0,
          hitRate: "—",
        };
        const summaryOrder: ("sellCount" | "buyCount" | "warnCount" | "hitRate")[] = [
          "sellCount",
          "buyCount",
          "warnCount",
          "hitRate",
        ];
        todayItems.forEach((item, i) => {
          const v = item.querySelector<HTMLElement>(".sb-today-value");
          if (!v) return;
          const key = summaryOrder[i];
          if (key === "hitRate") {
            v.textContent = String(summaryValues.hitRate);
          } else {
            v.textContent = `${summaryValues[key]} 件`;
          }
        });

        // --- サイドバー「月次コスト」を 0 にリセット（cost_logs 未配線のため暫定） ---
        const costVal = document.querySelector<HTMLElement>(".sb-cost-head .val");
        if (costVal) costVal.textContent = "¥0 / ¥5,000";
        const costFill = document.querySelector<HTMLElement>(".sb-cost-fill");
        if (costFill) costFill.style.width = "0%";
        const costSub = document.querySelector<HTMLElement>(".sb-cost-sub");
        if (costSub) costSub.textContent = "予算の 0%";

        // --- サイドバー：スパークライン / 累計30日 / 本日変動を「データなし」化 ---
        const spark = document.querySelector<HTMLElement>(".sb-spark");
        if (spark && !spark.querySelector(".sb-spark-empty")) {
          spark.innerHTML = `<div class="sb-spark-empty" style="height:48px;display:flex;align-items:center;justify-content:center;color:var(--ts-muted,#888);font-size:12px;">価格履歴データなし</div>`;
        }
        const cumul = document.querySelector<HTMLElement>(".sb-equity-cumulative .val");
        if (cumul) {
          cumul.textContent = "—";
          cumul.classList.remove("up", "down");
        }

        // --- 保有 Zone：ダミーカードを明示的に非表示（innerHTML 置換と二重で確実に） ---
        if (isHoldingsEmpty) {
          document.querySelectorAll<HTMLElement>(".holdings-list .hold").forEach((card) => {
            card.style.display = "none";
          });
        }

        // --- 警告パネル / 決算予定パネルを「データなし」化 ---
        const miniCards = document.querySelectorAll<HTMLElement>(".secondary-row .mini-card");
        miniCards.forEach((card) => {
          const titleText = card.querySelector(".mini-card-title")?.textContent ?? "";
          const count = card.querySelector<HTMLElement>(".mini-card-count");
          const isAlerts = titleText.includes("警告");
          const isEarnings = titleText.includes("決算");
          if (count) count.textContent = isEarnings ? "今週 0件" : "0件";
          // 中身（.alert や .info-list）を「データなし」プレースホルダに差し替える
          card
            .querySelectorAll<HTMLElement>(".alert, .info-list")
            .forEach((el, idx) => {
              if (idx > 0) {
                el.remove();
                return;
              }
              el.outerHTML = `<div class="mini-card-empty" style="padding:18px;color:var(--ts-muted,#888);font-size:13px;">${
                isAlerts ? "今日の警告はなし" : isEarnings ? "今週の決算予定はなし（カレンダー未配線）" : "データなし"
              }</div>`;
            });
        });

        // --- 予測検証（Track Record）を「データなし」化 ---
        document
          .querySelectorAll<HTMLElement>(".track-stats .track-stat")
          .forEach((stat) => {
            const v = stat.querySelector<HTMLElement>(".track-stat-value");
            const s = stat.querySelector<HTMLElement>(".track-stat-sub");
            if (v) {
              v.textContent = "—";
              v.classList.remove("up", "down");
            }
            if (s) s.textContent = "履歴なし";
          });

        // --- Topics：snapshot.topics があれば動的生成、無ければデータなし表示 ---
        const escapeHtml = (s: string): string =>
          s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
        const impLabel = (imp: string): string =>
          imp === "high" ? "重要" : imp === "medium" ? "中" : "低";
        type TopicItem = {
          id: number;
          importance: string;
          category: string;
          headline: string;
          summary: string;
          source: string;
          url: string;
          affected_tickers: string[];
          impact_text: string;
          collected_at: string;
        };
        const topics = ((snap as unknown) as {
          topics?: { counts: Record<string, number>; items: TopicItem[] };
        }).topics;

        if (topics && topics.items && topics.items.length > 0) {
          // タブの count を category 別に
          document.querySelectorAll<HTMLElement>(".topics-tabs .topics-tab").forEach((tab) => {
            const key = tab.getAttribute("data-topic-tab") ?? "all";
            const c = tab.querySelector<HTMLElement>(".count");
            if (c) c.textContent = String(topics.counts?.[key] ?? 0);
          });
          // リスト再生成（data-topic-content に応じて絞り込み）
          document.querySelectorAll<HTMLElement>(".topics-list").forEach((list) => {
            const filter = list.getAttribute("data-topic-content") ?? "all";
            const filtered =
              filter === "all"
                ? topics.items
                : topics.items.filter((t) => t.category === filter);
            if (filtered.length === 0) {
              list.innerHTML = `<div class="topics-empty" style="padding:24px;color:var(--ts-muted,#888);font-size:13px;">このカテゴリには Topics がありません。</div>`;
              return;
            }
            list.innerHTML = filtered
              .map((t) => {
                const tickers = t.affected_tickers.length
                  ? `<span class="topic-tickers" style="margin-left:8px;font-size:11px;opacity:0.7;">影響: ${t.affected_tickers.map(escapeHtml).join(" / ")}</span>`
                  : "";
                const urlLink = t.url
                  ? `<a href="${escapeHtml(t.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;">${escapeHtml(t.source)}</a>`
                  : escapeHtml(t.source);
                return `
                  <div class="topic imp-${escapeHtml(t.importance)}" data-cat="${escapeHtml(t.category)}">
                    <div class="topic-imp">
                      <span class="topic-imp-badge">${impLabel(t.importance)}</span>
                      <span class="topic-time">${escapeHtml(t.collected_at)}</span>
                    </div>
                    <div class="topic-body">
                      <div class="topic-headline">${escapeHtml(t.headline)}</div>
                      <div class="topic-summary">${escapeHtml(t.summary)}</div>
                      <div class="topic-meta" style="margin-top:6px;font-size:12px;opacity:0.7;">
                        <span class="topic-source">${urlLink}</span>
                        ${tickers}
                      </div>
                    </div>
                  </div>
                `;
              })
              .join("");
          });
        } else {
          // フォールバック：データなし
          document.querySelectorAll<HTMLElement>(".topics-tabs .count").forEach((c) => {
            c.textContent = "0";
          });
          document.querySelectorAll<HTMLElement>(".topics-list").forEach((list) => {
            if (!list.querySelector(".topics-empty")) {
              list.innerHTML = `<div class="topics-empty" style="padding:24px;color:var(--ts-muted,#888);font-size:13px;">Topics は朝バッチで収集されます（現在データなし）。</div>`;
            }
          });
        }

        // --- 決裁待ち decisions を buy-zone に動的描画（dashboard.html .act.buy 構造に準拠） ---
        type PendingDecision = {
          decision_id: number;
          ticker: string;
          name?: string;
          market?: string;
          sector?: string;
          action: string;
          status: string;
          gendo_stance: string;
          thesis: string;
          entry_price: number | null;
          stop_pct: number | null;
          target_period_days: number | null;
          score: number | null;
          expected_return: number | null;
          commander_recommendation: string | null;
          commander_counter: string | null;
          verdicts: Record<string, string>;
          price_history_30d?: number[];
          suggested_price?: number | null;
          recommended_shares?: number | null;
          stop_price?: number | null;
          target_price?: number | null;
          expected_return_pct?: number | null;
          estimated_cost_jpy?: number | null;
        };
        const pendingDecisions = ((snap as unknown) as {
          pending_decisions?: Record<string, PendingDecision>;
        }).pending_decisions ?? {};
        const pendingList = Object.values(pendingDecisions);

        // MAGI 3 審判の固定メタ（dashboard.html と完全に同じ色・役割名）
        const JUDGE_META: Record<string, { dot: string; role: string }> = {
          MELCHIOR: { dot: "#ff8c42", role: "業績" },
          BALTHASAR: { dot: "#4ecdc4", role: "株価" },
          CASPER: { dot: "#fbbf24", role: "文脈" },
        };
        const VD: Record<string, { word: string; color: string; dim: boolean }> = {
          buy: { word: "買", color: "var(--up)", dim: false },
          warn: { word: "慎重", color: "var(--warn)", dim: false },
          hold: { word: "中立", color: "var(--ink-2)", dim: false },
          sell: { word: "売", color: "var(--down)", dim: false },
          na: { word: "不能", color: "var(--ink-3)", dim: true },
        };

        const buyZoneItems = document.querySelector<HTMLElement>(".buy-zone .zone-items");
        // v2.10: 決裁待ちが多いと buy-zone が縦に膨張するので、4 件メイン + 残りは折りたたみ
        const DECISIONS_TOP_LIMIT = 4;
        const renderPendingItem = (d: PendingDecision) => {
              const stance = d.gendo_stance || "—";
              const market = d.market || "JP";
              const sector = d.sector || "";
              const name = d.name || "";
              const summary = d.commander_recommendation
                ? d.commander_recommendation
                : d.thesis || "MAGI 評価待ち";

              // MAGI dots（dashboard.html と同一マークアップ）
              const judges = ["MELCHIOR", "BALTHASAR", "CASPER"]
                .map((j) => {
                  const meta = JUDGE_META[j];
                  const verdict = d.verdicts?.[j] ?? "na";
                  const vd = VD[verdict] ?? VD.na;
                  const dotStyle = vd.dim
                    ? `background:${meta.dot};opacity:0.45`
                    : `background:${meta.dot}`;
                  return `<span class="mmd"><span class="dot" style="${dotStyle}"></span><span class="jn">${j}</span><span class="jr">${meta.role}</span><span class="vd" style="color:${vd.color}">${escapeHtml(vd.word)}</span></span>`;
                })
                .join("");

              // magi-mini-state：voting=MELCHIOR+CASPER の合意度
              const vMel = d.verdicts?.MELCHIOR ?? "na";
              const vCas = d.verdicts?.CASPER ?? "na";
              const buyVotes = [vMel, vCas].filter((v) => v === "buy").length;
              const naCount = [vMel, vCas].filter((v) => v === "na").length;
              const stateText =
                naCount > 0
                  ? `${naCount}/2 判定不能`
                  : buyVotes === 2
                    ? "全員一致・買い"
                    : buyVotes === 1
                      ? "1/2 買い・割れ"
                      : "買いゼロ";

              const sqlCmd = `UPDATE decisions SET status='approved' WHERE id=${d.decision_id};`;

              // 価格推移 sparkline（過去 30 日の終値から生成）
              const ph = d.price_history_30d ?? [];
              let forecastBlock = "";
              if (ph.length >= 2) {
                const first = ph[0];
                const last = ph[ph.length - 1];
                const minV = Math.min(...ph);
                const maxV = Math.max(...ph);
                const range = maxV - minV || 1;
                const W = 320;
                const H = 60;
                const pts = ph
                  .map((v, i) => {
                    const x = (i / (ph.length - 1)) * (W - 4) + 2;
                    const y = H - ((v - minV) / range) * (H - 8) - 4;
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ");
                const pct = first > 0 ? ((last - first) / first) * 100 : 0;
                const isUp = pct >= 0;
                const lineColor = isUp ? "#4ade80" : "#f87171";
                const fillColor = isUp ? "#4ade8022" : "#f8717122";
                const sign = isUp ? "+" : "";
                const lastX = (W - 4) + 2;
                const lastY = H - ((last - minV) / range) * (H - 8) - 4;
                const areaPts = `2,${H} ${pts} ${lastX.toFixed(1)},${H}`;
                const isJp = (d.market || "JP") === "JP";
                const priceStr = isJp
                  ? `¥${Math.round(last).toLocaleString()}`
                  : `$${last.toFixed(2)}`;
                forecastBlock = `
                  <div class="forecast" style="margin-top:10px;">
                    <div class="forecast-meta">
                      <span class="lbl">30日前</span>
                      <span class="val" style="color:${lineColor};font-weight:600;">${sign}${pct.toFixed(1)}% / 現在 ${priceStr}</span>
                      <span class="lbl">${ph.length}日分</span>
                    </div>
                    <svg class="forecast-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:60px;">
                      <line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#6b6962" stroke-width="0.4" stroke-dasharray="2,2" opacity="0.5"/>
                      <polygon points="${areaPts}" fill="${fillColor}"/>
                      <polyline fill="none" stroke="${lineColor}" stroke-width="1.6" points="${pts}"/>
                      <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="3" fill="${lineColor}"/>
                    </svg>
                  </div>
                `;
              } else {
                forecastBlock = `
                  <div class="forecast" style="margin-top:10px;">
                    <div class="forecast-meta">
                      <span class="lbl">価格履歴データなし（yfinance 取得失敗）</span>
                    </div>
                  </div>
                `;
              }

              const fmtJpy = (n: number | null | undefined) =>
                n == null ? "—" : "¥" + Math.round(n).toLocaleString("ja-JP");
              const _priceFmt = fmtJpy(d.suggested_price);
              const _stopFmt = fmtJpy(d.stop_price);
              const _sharesVal =
                d.recommended_shares != null && d.recommended_shares > 0
                  ? String(d.recommended_shares)
                  : "";
              const _priceInputVal =
                d.suggested_price != null ? String(Math.round(d.suggested_price)) : "";

              return `
                <div class="act buy" data-detail="d${d.decision_id}">
                  <div class="act-top">
                    <div class="gendo-ind">
                      <div class="g-face" aria-hidden="true">碇</div>
                      <div class="g-word">${escapeHtml(stance)}</div>
                      <div class="g-lbl">GENDO</div>
                    </div>
                    <div>
                      <div class="act-head">
                        <span class="act-ticker">${escapeHtml(d.ticker)}</span>
                        <span class="act-market">${escapeHtml(market)}${sector ? " " + escapeHtml(sector) : ""}</span>
                        ${name ? `<span class="act-name">${escapeHtml(name)}</span>` : ""}
                      </div>
                      <div class="act-summary">${escapeHtml(summary)}</div>
                      <div class="magi-mini">
                        <div class="magi-mini-dots">${judges}</div>
                        <span class="magi-mini-state">${escapeHtml(stateText)}</span>
                      </div>
                      <div class="cmd-line" style="margin-top:6px;font-size:11px;opacity:0.85;">承認: <code style="font-family:'JetBrains Mono',ui-monospace,monospace;font-size:10px;user-select:all;">${escapeHtml(sqlCmd)}</code></div>
                      <div class="act-order" style="display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin-top:8px;font-size:12px;">
                        <span style="opacity:.75;">想定 <b>${_priceFmt}</b></span>
                        <span style="opacity:.75;">stop <b>${_stopFmt}</b></span>
                        <label style="display:inline-flex;align-items:center;gap:4px;opacity:.9;">株数 <input type="number" min="0" inputmode="numeric" data-shares-input value="${_sharesVal}" placeholder="手入力" style="width:62px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.18);color:inherit;border-radius:4px;padding:3px 6px;font-size:12px;"></label>
                        <label style="display:inline-flex;align-items:center;gap:4px;opacity:.9;">単価 <input type="number" min="0" step="0.1" inputmode="decimal" data-price-input value="${_priceInputVal}" style="width:76px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.18);color:inherit;border-radius:4px;padding:3px 6px;font-size:12px;"></label>
                      </div>
                      <div class="act-actions" style="margin-top:8px;display:flex;gap:8px;align-items:center;">
                        <button type="button" class="act-mark-filled" data-mark-filled data-decision-id="${d.decision_id}" data-ticker="${escapeHtml(d.ticker)}" style="display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#4ade80,#16a085);color:#fff;border:none;border-radius:6px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:0.3px;">✓ 約定を記録</button>
                        <button type="button" class="act-skip" data-skip-decision data-decision-id="${d.decision_id}" data-ticker="${escapeHtml(d.ticker)}" style="background:transparent;color:var(--ink-2,#8893a5);border:1px solid rgba(255,255,255,.18);border-radius:6px;padding:8px 12px;font-size:12px;cursor:pointer;">見送り</button>
                      </div>
                    </div>
                    <div class="act-arrow">→</div>
                  </div>
                  ${forecastBlock}
                </div>
              `;
        };

        if (buyZoneItems && pendingList.length > 0) {
          const visibleList = pendingList.slice(0, DECISIONS_TOP_LIMIT);
          const overflowList = pendingList.slice(DECISIONS_TOP_LIMIT);
          const fillAllBar = `<div class="fill-all-bar" style="margin-bottom:10px;"><button type="button" data-fill-all style="width:100%;background:linear-gradient(135deg,#4ade80,#16a085);color:#fff;border:none;border-radius:8px;padding:10px;font-size:13px;font-weight:700;cursor:pointer;">✓ 全約定（株数を入力した銘柄をまとめて記録）</button></div>`;
          let html = fillAllBar + visibleList.map(renderPendingItem).join("");
          if (overflowList.length > 0) {
            const overflowHtml = overflowList.map(renderPendingItem).join("");
            html += `
              <button class="decisions-overflow-toggle" data-decisions-overflow-toggle>
                <span class="ovt-icon">+</span>
                <span class="ovt-label">残り <span class="decisions-overflow-count">${overflowList.length}</span> 件の決裁待ちを表示</span>
              </button>
              <div class="decisions-overflow" data-decisions-overflow="collapsed">
                ${overflowHtml}
              </div>
            `;
          }
          buyZoneItems.innerHTML = html;
        }

        // --- 詳細パネル：snapshot.candidates に対応 ID が無いものは非表示 ---
        const visiblePanelIds = new Set(candIdsForSummary);
        document
          .querySelectorAll<HTMLElement>(".detail-panel[data-panel]")
          .forEach((p) => {
            const id = p.getAttribute("data-panel") ?? "";
            if (!visiblePanelIds.has(id)) {
              p.style.display = "none";
            }
          });

        // --- buy/sell zone のヘッダ count を実データに（pending_decisions も含む）---
        const buyCount = candIdsForSummary.length + pendingList.length;
        const sellCount = summaryValues.sellCount;
        const buyZoneCount = document.querySelector<HTMLElement>(".buy-zone .zone-count");
        if (buyZoneCount) {
          buyZoneCount.innerHTML =
            buyCount === 0 ? "0件" : `<strong>${buyCount}</strong>件 決裁待ち`;
        }
        const sellZoneCount = document.querySelector<HTMLElement>(".sell-zone .zone-count");
        if (sellZoneCount) {
          sellZoneCount.innerHTML =
            sellCount === 0 ? "0件" : `<strong>${sellCount}</strong>件 推奨`;
        }

        // --- サイドバー「今日のサマリー」の買い推奨を pending 含む値に再上書き ---
        const buyTodayValue = todayItems[1]?.querySelector<HTMLElement>(".sb-today-value");
        if (buyTodayValue) buyTodayValue.textContent = `${buyCount} 件`;

        // --- ナビバッジ（ダッシュボードの "4"）も 0 件に ---
        document.querySelectorAll<HTMLElement>(".sb-nav .nav-badge").forEach((b) => {
          // 数値らしきテキストだけ 0 化（ZEELE の「攻め」など文字バッジは触らない）
          if (/^\d+$/.test(b.textContent?.trim() ?? "")) {
            b.textContent = "0";
          }
        });

        // 売り Zone：snapshot.sell に無い ID は非表示（保有 0 なら全部）。
        document
          .querySelectorAll<HTMLElement>(".sell-zone .act")
          .forEach((act) => {
            const id = act.getAttribute("data-detail") ?? "";
            if (isHoldingsEmpty || !sellIds.has(id)) {
              act.style.display = "none";
            }
          });
        document
          .querySelectorAll<HTMLElement>(".sell-zone .mini-act")
          .forEach((m) => {
            const id = m.getAttribute("data-detail") ?? "";
            if (isHoldingsEmpty || !sellIds.has(id)) {
              m.style.display = "none";
            }
          });

        // 売り Zone 自体が空になったら、プレースホルダー文言に差し替える。
        const sellZone = document.querySelector<HTMLElement>(".sell-zone");
        if (
          sellZone &&
          sellZone.querySelectorAll<HTMLElement>(".act:not([style*='display: none'])").length === 0 &&
          !sellZone.querySelector(".sell-empty")
        ) {
          const sub = sellZone.querySelector(".zone-subtitle");
          const empty = document.createElement("div");
          empty.className = "sell-empty";
          empty.style.cssText =
            "padding:24px;color:var(--ts-muted,#888);font-size:13px;line-height:1.6;";
          empty.textContent = isHoldingsEmpty
            ? "保有なし — 売り推奨はありません（買い → 紙約定で保有が入ると、ここに MAGI 売り判定が並びます）"
            : "今日の MAGI 売り推奨はなし（保有は規律 OK 状態）";
          (sub?.parentElement ?? sellZone).appendChild(empty);
        }

        // 買い Zone：snapshot.candidates に無い data-detail は非表示。
        // ただし `d<N>` プレフィックスは決裁待ち動的カード（後段で innerHTML 置換で挿入）
        // なので非表示対象から除外する。
        const candIds = new Set(Object.keys(snap.candidates ?? {}));
        const isPendingCard = (id: string) => /^d\d+$/.test(id);
        document
          .querySelectorAll<HTMLElement>(".buy-zone .act")
          .forEach((act) => {
            const id = act.getAttribute("data-detail") ?? "";
            if (!candIds.has(id) && !isPendingCard(id)) {
              act.style.display = "none";
            }
          });
        document
          .querySelectorAll<HTMLElement>(".buy-zone .mini-act")
          .forEach((m) => {
            const id = m.getAttribute("data-detail") ?? "";
            if (!candIds.has(id) && !isPendingCard(id)) {
              m.style.display = "none";
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
