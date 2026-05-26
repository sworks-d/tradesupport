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
  zeele_entered_at?: string; // ISO date
  zeele_weeks?: number;
  period_return_pct?: number; // 12週リターン
  price_history_12w?: number[]; // 12週分の終値
  promoted?: boolean;
};

type Snapshot = {
  zeele?: { candidates?: Candidate[] };
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
  const W = 110;
  const H = 28;
  const { line, area } = buildSparklinePath(values, W, H);
  const positive = (returnPct ?? values[values.length - 1] - values[0]) >= 0;
  const stroke = positive ? "var(--up)" : "var(--down)";
  const fill = positive ? "rgba(74,222,128,0.12)" : "rgba(248,113,113,0.12)";
  return (
    <div className="zeele-spark">
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        className="zeele-spark-svg"
        aria-hidden
      >
        <path d={area} fill={fill} />
        <path d={line} stroke={stroke} strokeWidth={1.4} fill="none" />
        <circle
          cx={W}
          cy={
            H -
            ((values[values.length - 1] - Math.min(...values)) /
              (Math.max(...values) - Math.min(...values) || 1)) *
              H
          }
          r={2.2}
          fill={stroke}
        />
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
  const visible = candidates.slice(0, 4);

  const handlePromote = (ticker: string) => {
    setPromotedLocal((prev) => {
      const next = new Set(prev);
      next.add(ticker);
      return next;
    });
  };

  const isPromoted = (c: Candidate): boolean =>
    !!c.promoted || promotedLocal.has(c.ticker);

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

      <div className="zeele-body">
        {visible.length === 0 ? (
          <div className="zeele-card-note">
            熟成中の候補なし（screening の通算履歴と紐付け配線は次セッション）
          </div>
        ) : (
          visible.map((c) => {
            const promoted = isPromoted(c);
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
                  <Sparkline
                    values={c.price_history_12w}
                    returnPct={c.period_return_pct}
                  />
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
          })
        )}
        {candidates.length > visible.length && (
          <div className="zeele-card-note" style={{ marginTop: 8 }}>
            ほか {candidates.length - visible.length} 件 ／ 詳細探索は F5
          </div>
        )}
      </div>
    </>,
    mount,
  );
}
