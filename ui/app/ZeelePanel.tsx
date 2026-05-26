"use client";

/**
 * ZEELE Zone — 熟成中の攻め候補（売り/買いの左カラム）
 *
 * 設計（memory: zeele-magi-role-split）:
 *   - ZEELE = 攻めの銘柄レコメンド（**数週〜月単位で熟成中**の厳選候補）
 *   - MAGI = 守り（保有規律・posture・売り買い決裁）
 *
 * 思想（user 確定 2026-05-26）:
 *   - 毎日銘柄を買うわけではない → ZEELE は1日で出入りしない
 *   - 数週間以上滞留する候補だけを並べる
 *   - 熱さの基準は "1日の bump" ではなく "数週の継続性"
 *   - 量より質：3-5銘柄で十分・空き枠でも OK
 *
 * UI:
 *   - dashboard.html の .action-zone 内・売り/買いの左カラム
 *   - 各カードに 12 週分の sparkline で「継続性」を視覚化
 *   - ZEELE 滞留週数を表示（"ZEELE入り 5/8〜 (3週)"）
 *   - 構造的根拠（structural_thesis）を narrative の補強として表示
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Candidate = {
  ticker: string;
  name?: string;
  preset?: string;
  narrative?: string;
  structural_thesis?: string;
  reference_score?: number;
  x_sentiment?: string;
  zeele_entered_at?: string;
  zeele_weeks?: number;
  period_return_pct?: number;
  price_history_12w?: number[];
  // 価格・推奨サイジング（D-23 準拠・参考値）
  last_price?: number;
  last_price_jpy?: number;
  suggested_jpy?: number;
  suggested_shares?: number;
  sizing_constraint?: "risk" | "cap" | "cash" | "n/a";
  stop_pct_used?: number;
  stop_pct_source?: "preset" | "vol" | "default";
  promoted?: boolean;
};

type Snapshot = {
  zeele?: {
    candidates?: Candidate[];
    account_total_jpy?: number;
    available_cash_jpy?: number;
    investable_cash_jpy?: number;
    cash_floor_jpy?: number;
  };
};

const PRESET_LABEL: Record<string, string> = {
  value: "バリュー",
  growth: "グロース",
  momentum: "モメンタム",
  contrarian: "コントラリアン",
  alpha: "アルファ",
  pullback: "押し目",
  "growth-value": "グロース×バリュー",
};

/** 12週分の終値から SVG path を組み立てる */
function buildSparklinePath(
  values: number[],
  width: number,
  height: number,
): { line: string; area: string } {
  if (values.length < 2) return { line: "", area: "" };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * height;
    return [x, y] as const;
  });
  const line = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
  // 面（下を埋める）
  const [lastX] = points[points.length - 1];
  const area = `${line} L${lastX.toFixed(1)},${height} L0,${height} Z`;
  return { line, area };
}

function Sparkline({
  values,
  returnPct,
}: {
  values: number[];
  returnPct?: number;
}) {
  if (!values || values.length < 2) return null;
  // 内部 viewBox は固定・CSS で card 幅にストレッチ。preserveAspectRatio=none で歪み許容。
  const W = 400;
  const H = 56;
  const { line, area } = buildSparklinePath(values, W, H);
  const positive = (returnPct ?? values[values.length - 1] - values[0]) >= 0;
  const stroke = positive ? "var(--up)" : "var(--down)";
  const fill = positive ? "rgba(74,222,128,0.14)" : "rgba(248,113,113,0.14)";
  const lastY =
    H -
    ((values[values.length - 1] - Math.min(...values)) /
      (Math.max(...values) - Math.min(...values) || 1)) *
      H;
  return (
    <div className="zeele-spark">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="zeele-spark-svg"
        aria-hidden
      >
        <path d={area} fill={fill} />
        <path d={line} stroke={stroke} strokeWidth={2} fill="none" vectorEffect="non-scaling-stroke" />
        <circle cx={W - 2} cy={lastY} r={3} fill={stroke} />
      </svg>
      {returnPct !== undefined && (
        <div className={`zeele-return ${positive ? "up" : "down"}`}>
          {positive ? "+" : ""}
          {returnPct.toFixed(1)}% <span className="period">/ 12週</span>
        </div>
      )}
    </div>
  );
}

export default function ZeelePanel() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [mount, setMount] = useState<HTMLElement | null>(null);
  const [promotedLocal, setPromotedLocal] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState(false);

  // dashboard.html 内の #zeele-zone-mount を React Portal ターゲットに使う。
  useEffect(() => {
    let canceled = false;
    let attempts = 0;
    const tick = () => {
      if (canceled) return;
      const el = document.getElementById("zeele-zone-mount");
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

  if (!mount) return null;
  const candidates = data?.zeele?.candidates ?? [];
  const VISIBLE_COUNT = 2; // 上位N件のみ初期表示。残りは expand で展開
  const visibleAlways = candidates.slice(0, VISIBLE_COUNT);
  const collapsed = candidates.slice(VISIBLE_COUNT);

  const handlePromote = (ticker: string) => {
    setPromotedLocal((prev) => {
      const next = new Set(prev);
      next.add(ticker);
      return next;
    });
  };

  const isPromoted = (c: Candidate): boolean =>
    !!c.promoted || promotedLocal.has(c.ticker);

  const fmtPrice = (t: string, p: number | undefined): string => {
    if (p === undefined) return "";
    // JP は 4桁数字、US は英字。¥ or $ で表示。
    const isJP = /^\d{4}$/.test(t);
    return isJP
      ? `¥${Math.round(p).toLocaleString()}`
      : `$${p.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  };

  const renderCard = (c: Candidate) => {
    const promoted = isPromoted(c);
    const last = c.price_history_12w?.[c.price_history_12w.length - 1];
    return (
      <article className="zeele-card" key={c.ticker}>
        <div className="zeele-card-head">
          <div>
            <span className="zeele-ticker">{c.ticker}</span>
            {c.name && <span className="zeele-name">{c.name}</span>}
          </div>
          {c.preset && (
            <span className="zeele-preset">
              {PRESET_LABEL[c.preset] ?? c.preset}
            </span>
          )}
        </div>

        {c.price_history_12w && (
          <Sparkline values={c.price_history_12w} returnPct={c.period_return_pct} />
        )}

        {last !== undefined && (
          <div className="zeele-price">
            <span className="zeele-price-now">{fmtPrice(c.ticker, last)}</span>
            <span className="zeele-price-label">現在値</span>
          </div>
        )}

        {c.suggested_jpy !== undefined && c.suggested_jpy > 0 && (
          <div
            className="zeele-sizing"
            title={
              `D-23 準拠サイジング\n` +
              `stop ${((c.stop_pct_used ?? 0.12) * 100).toFixed(0)}% ` +
              `(${
                c.stop_pct_source === "vol"
                  ? "12週ボラ × 4σ"
                  : c.stop_pct_source === "preset"
                  ? `${c.preset ?? "default"} 既定`
                  : "既定 12%"
              })\n` +
              `risk ¥2,000 / cap ¥20,000 / 投入可能 ¥${(data?.zeele?.investable_cash_jpy ?? 0).toLocaleString()}\n` +
              `→ ${
                c.sizing_constraint === "risk"
                  ? "risk が頭打ち（stop が広い銘柄ほど少なく）"
                  : c.sizing_constraint === "cap"
                  ? "1銘柄上限 20% が頭打ち"
                  : "現金可用額が頭打ち"
              }`
            }
          >
            <span className="zeele-sizing-label">推奨</span>
            <span className="zeele-sizing-jpy">
              ¥{(c.suggested_jpy ?? 0).toLocaleString()}
            </span>
            <span className="zeele-sizing-detail">
              ({c.suggested_shares}株・stop{((c.stop_pct_used ?? 0.12) * 100).toFixed(0)}%
              {c.stop_pct_source === "vol" && (
                <span className="zeele-sizing-vol" title="実現ボラから動的算出"> ボラ由来</span>
              )}
              ・
              {c.sizing_constraint === "risk"
                ? "risk上限"
                : c.sizing_constraint === "cap"
                ? "サイズ上限"
                : "現金上限"})
            </span>
          </div>
        )}

        {c.narrative && <div className="zeele-narrative">{c.narrative}</div>}
        {c.structural_thesis && (
          <div className="zeele-thesis">{c.structural_thesis}</div>
        )}

        <div className="zeele-meta">
          {c.zeele_entered_at && (
            <span>
              ZEELE 入り {c.zeele_entered_at.slice(5)} (
              <b>{c.zeele_weeks ?? 0}週</b>)
            </span>
          )}
          {c.reference_score !== undefined && (
            <span>
              参考 <b>{c.reference_score}</b>
            </span>
          )}
        </div>

        <div className="zeele-actions">
          <button
            type="button"
            className={`zeele-btn-promote${promoted ? " promoted" : ""}`}
            onClick={() => !promoted && handlePromote(c.ticker)}
            disabled={promoted}
          >
            {promoted ? "✓ 昇格済" : "→ watchlist 昇格"}
          </button>
        </div>
      </article>
    );
  };

  return createPortal(
    <>
      <div className="zone-head">
        <div className="zone-title">
          ZEELE <span className="en">Explore · 攻め</span>
        </div>
        <div className="zone-count">
          <strong>{candidates.length}</strong>件 熟成中
        </div>
      </div>
      <div className="zeele-warning">
        ⚠ 参考・未照合 ／ 数週単位で熟成中の候補（毎日は変わらない）
      </div>
      {data?.zeele?.investable_cash_jpy !== undefined && (
        <div className="zeele-budget" title="投入可能 = 現金 − 現金下限20%（D-23 #4）">
          投入可能 <b>¥{(data.zeele.investable_cash_jpy ?? 0).toLocaleString()}</b>
          <span className="zeele-budget-sub">
            （現金 ¥{(data.zeele.available_cash_jpy ?? 0).toLocaleString()} − 下限 ¥
            {(data.zeele.cash_floor_jpy ?? 0).toLocaleString()}）
          </span>
        </div>
      )}

      <div className="zeele-body">
        {candidates.length === 0 ? (
          <div className="zeele-card-note">
            熟成中の候補なし（screening の通算履歴と紐付け配線は次セッション）
          </div>
        ) : (
          <>
            {visibleAlways.map(renderCard)}

            {collapsed.length > 0 && (
              <>
                <button
                  type="button"
                  className={`zeele-more-toggle${expanded ? " open" : ""}`}
                  onClick={() => setExpanded((v) => !v)}
                  aria-expanded={expanded}
                >
                  <span className="zeele-more-icon">{expanded ? "−" : "+"}</span>
                  {expanded ? "閉じる" : `他の候補 ${collapsed.length}件を見る`}
                </button>
                {expanded && (
                  <div className="zeele-more-list">{collapsed.map(renderCard)}</div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </>,
    mount,
  );
}
