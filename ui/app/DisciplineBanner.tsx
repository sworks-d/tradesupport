"use client";

/**
 * Discipline Banner — D-24 / D-25 / X-2 を UI に反映する追加層。
 *
 * 配置：ダッシュボードの最上部（朝5分レビューで最初に見るゾーン）。
 * 情報を3列に分散：
 *   - 今日のポスチャー（exposure_coach）
 *   - 保有規律（holding_health_summary）
 *   - テーゼ統計（theses_summary）
 * フッター行：北極星脚注 + D-25 市場対象比率
 *
 * 既存ダッシュボード（dashboard.html）は一切変更しない（D-19 ガード）。
 * snapshot.json の新フィールドが存在しない場合は何も描画しない（後方互換）。
 */

import { useEffect, useState } from "react";

type Exposure = {
  recommendation: "NEW_ENTRY_ALLOWED" | "REDUCE_ONLY" | "CASH_PRIORITY";
  bias: "GROWTH" | "VALUE" | "NEUTRAL";
  participation: "BROAD" | "NARROW" | "UNKNOWN";
  confidence: "HIGH" | "MEDIUM" | "LOW";
  ceiling_pct: number;
  rationale: string;
  inputs_provided: string[];
  inputs_missing: string[];
};

type HealthSummary = { OK?: number; WARN?: number; REVIEW?: number };

type ThesesSummary = {
  counts: Record<string, number>;
  total: number;
  active: number;
};

type NorthStar = { name: string; url: string; mantra: string };

type MarketFocus = {
  primary: string;
  primary_weight_pct: number;
  satellite: string;
  satellite_weight_pct: number;
  decision_id: string;
};

type BannerData = {
  generated_at?: string;
  exposure?: Exposure;
  holding_health_summary?: HealthSummary;
  theses_summary?: ThesesSummary;
  north_star?: NorthStar;
  market_focus?: MarketFocus;
};

const POSTURE_COLOR: Record<Exposure["recommendation"], string> = {
  NEW_ENTRY_ALLOWED: "#16a085",
  REDUCE_ONLY: "#e67e22",
  CASH_PRIORITY: "#c0392b",
};

const POSTURE_LABEL: Record<Exposure["recommendation"], string> = {
  NEW_ENTRY_ALLOWED: "新規エントリー可",
  REDUCE_ONLY: "新規控え（既存のみ）",
  CASH_PRIORITY: "現金優先",
};

const CONFIDENCE_LABEL: Record<Exposure["confidence"], string> = {
  HIGH: "確度 高",
  MEDIUM: "確度 中",
  LOW: "確度 低",
};

export default function DisciplineBanner() {
  const [data, setData] = useState<BannerData | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/data/snapshot.json?t=${Date.now()}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: BannerData | null) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        // snapshot 不在は静かに何も描画しない（後方互換）
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data || !data.exposure) return null;

  const exp = data.exposure;
  const health = data.holding_health_summary ?? {};
  const theses = data.theses_summary;
  const north = data.north_star;
  const focus = data.market_focus;

  const reviewCount = health.REVIEW ?? 0;
  const warnCount = health.WARN ?? 0;
  const okCount = health.OK ?? 0;
  const healthHasIssues = reviewCount > 0 || warnCount > 0;

  return (
    <section
      style={{
        gridColumn: "1 / -1",
        background: "#fafaf8",
        border: "1px solid #d8dde2",
        borderRadius: 10,
        padding: "14px 18px",
        marginBottom: 14,
        fontFamily:
          "'Inter','Noto Sans JP',-apple-system,BlinkMacSystemFont,sans-serif",
        fontSize: 13,
        color: "#222",
      }}
      aria-label="規律バナー（exposure / 保有ヘルス / テーゼ）"
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.4fr 1fr 1fr",
          gap: 16,
          alignItems: "stretch",
        }}
      >
        {/* === 今日のポスチャー（最も視覚的に強い） === */}
        <div
          style={{
            borderLeft: `4px solid ${POSTURE_COLOR[exp.recommendation]}`,
            paddingLeft: 12,
          }}
        >
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.05em",
              color: "#666",
              fontWeight: 700,
            }}
          >
            今日のポスチャー（X-2C exposure_coach）
          </div>
          <div
            style={{
              fontSize: 18,
              fontWeight: 700,
              color: POSTURE_COLOR[exp.recommendation],
              marginTop: 4,
              marginBottom: 4,
            }}
          >
            {POSTURE_LABEL[exp.recommendation]}
          </div>
          <div style={{ fontSize: 11.5, color: "#555", lineHeight: 1.5 }}>
            <span style={{ marginRight: 10 }}>
              ceiling: <b>{exp.ceiling_pct}%</b>
            </span>
            <span style={{ marginRight: 10 }}>{CONFIDENCE_LABEL[exp.confidence]}</span>
            <span style={{ marginRight: 10 }}>
              参加幅: {exp.participation === "UNKNOWN" ? "—" : exp.participation}
            </span>
            <span>傾向: {exp.bias === "NEUTRAL" ? "中立" : exp.bias}</span>
          </div>
          <div
            style={{
              fontSize: 11,
              color: "#888",
              marginTop: 4,
              fontStyle: "italic",
            }}
          >
            {exp.rationale}
          </div>
        </div>

        {/* === 保有規律（T1-T5）=== */}
        <div
          style={{
            borderLeft: `4px solid ${healthHasIssues ? "#c0392b" : "#16a085"}`,
            paddingLeft: 12,
          }}
        >
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.05em",
              color: "#666",
              fontWeight: 700,
            }}
          >
            保有規律（X-2B Kanchi T1-T5）
          </div>
          <div style={{ display: "flex", gap: 14, marginTop: 8 }}>
            <HealthChip label="OK" count={okCount} color="#16a085" />
            <HealthChip label="WARN" count={warnCount} color="#e67e22" />
            <HealthChip label="REVIEW" count={reviewCount} color="#c0392b" />
          </div>
          <div style={{ fontSize: 11, color: "#888", marginTop: 6 }}>
            {okCount + warnCount + reviewCount === 0
              ? "保有なし（現金100%）"
              : reviewCount > 0
              ? `${reviewCount}件が見直しキュー（auto-sellなし・人間判断）`
              : warnCount > 0
              ? `${warnCount}件が注意（次回チェックで再評価）`
              : "全保有 OK（次回スキャンで再評価）"}
          </div>
        </div>

        {/* === テーゼ統計（ライフサイクル） === */}
        <div
          style={{
            borderLeft: "4px solid #8e44ad",
            paddingLeft: 12,
          }}
        >
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.05em",
              color: "#666",
              fontWeight: 700,
            }}
          >
            テーゼ（X-2A thesis_store）
          </div>
          {theses ? (
            <>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 700,
                  color: "#5b2c6f",
                  marginTop: 4,
                  marginBottom: 4,
                }}
              >
                ACTIVE <b>{theses.active}</b> 件
              </div>
              <div style={{ fontSize: 11.5, color: "#555" }}>
                IDEA {theses.counts.IDEA ?? 0} / ENTRY_READY{" "}
                {theses.counts.ENTRY_READY ?? 0} / CLOSED{" "}
                {theses.counts.CLOSED ?? 0}
              </div>
              <div style={{ fontSize: 11, color: "#888", marginTop: 6 }}>
                ライフサイクル：IDEA → ENTRY_READY → ACTIVE → CLOSED
              </div>
            </>
          ) : (
            <div style={{ fontSize: 11.5, color: "#888", marginTop: 8 }}>
              テーゼなし
            </div>
          )}
        </div>
      </div>

      {/* === フッター行：北極星脚注 + 市場対象 === */}
      {(north || focus) && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderTop: "1px solid #e3e6ea",
            marginTop: 12,
            paddingTop: 10,
            fontSize: 11,
            color: "#666",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          {north && (
            <span>
              北極星：{" "}
              <a
                href={north.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#444", textDecoration: "none", fontWeight: 600 }}
              >
                {north.name}
              </a>{" "}
              <span style={{ color: "#888", fontStyle: "italic" }}>
                「{north.mantra}」
              </span>{" "}
              <span style={{ color: "#aaa" }}>(D-24)</span>
            </span>
          )}
          {focus && (
            <span>
              市場対象：<b>{focus.primary}</b> {focus.primary_weight_pct}% /{" "}
              {focus.satellite} {focus.satellite_weight_pct}%{" "}
              <span style={{ color: "#aaa" }}>({focus.decision_id})</span>
            </span>
          )}
        </div>
      )}
    </section>
  );
}

function HealthChip({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: count > 0 ? color : "#aaa",
          lineHeight: 1,
        }}
      >
        {count}
      </div>
      <div
        style={{
          fontSize: 10,
          color: count > 0 ? color : "#aaa",
          marginTop: 2,
          letterSpacing: "0.05em",
        }}
      >
        {label}
      </div>
    </div>
  );
}
