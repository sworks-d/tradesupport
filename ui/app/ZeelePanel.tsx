"use client";

/**
 * ZEELE Panel — 攻めの銘柄レコメンド（左スライドアウト）。
 *
 * 設計（memory: zeele-magi-role-split / claude-trading-skills-north-star）:
 *   - ZEELE = 攻め（新規買い候補・narrative・edge候補）
 *   - MAGI = 守り（保有規律・posture・売り判断）
 *
 * UI:
 *   - 左端の縦書きトリガーを常時表示。クリックで左から panel をスライドイン
 *   - 右の MAGI 詳細パネル（DetailPanels.tsx）と鏡像対称
 *   - 紫アクセント + 破線で「参考・未照合」を視覚的に主張
 *   - 既存 dashboard.html は無改変（D-19 ガード）
 *
 * データ:
 *   - /data/snapshot.json の `zeele.candidates[]` を読む
 *   - 各候補に [→ watchlist 昇格] ボタン。クリックで snapshot 内 promoted=true を即時反映（暫定）
 *
 * 制約:
 *   - 数値（reference_score 等）は「参考」として表示するが「決裁」には使わない
 *   - watchlist 昇格後の MAGI 判定は翌朝バッチで実施（人間ゲートが唯一の橋）
 */

import { useEffect, useState } from "react";

type Candidate = {
  ticker: string;
  name?: string;
  preset?: string;          // value / growth / momentum / contrarian / alpha など
  narrative?: string;
  reference_score?: number;  // 「参考スコア」（未照合・SCORE:NONE 適合）
  x_sentiment?: string;
  promoted?: boolean;
};

type NarrativeItem = {
  title: string;
  summary?: string;
  source?: string;
};

type ZeeleData = {
  candidates?: Candidate[];
  narrative_themes?: NarrativeItem[];
  x_trends?: NarrativeItem[];
  generated_at?: string;
};

type Snapshot = {
  zeele?: ZeeleData;
  north_star?: { name: string; url: string; mantra: string };
  market_focus?: {
    primary: string;
    primary_weight_pct: number;
    satellite: string;
    satellite_weight_pct: number;
    decision_id: string;
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

type SegKey = "all" | "candidates" | "narrative" | "trends";

const SEGMENTS: { key: SegKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "candidates", label: "候補" },
  { key: "narrative", label: "narrative" },
  { key: "trends", label: "X トレンド" },
];

export default function ZeelePanel() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Snapshot | null>(null);
  const [seg, setSeg] = useState<SegKey>("all");
  const [promotedLocal, setPromotedLocal] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    fetch("/data/snapshot.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Snapshot | null) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        // snapshot 不在は静かに空表示（後方互換）
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const zeele = data?.zeele;
  const candidates = zeele?.candidates ?? [];
  const themes = zeele?.narrative_themes ?? [];
  const trends = zeele?.x_trends ?? [];

  const showCandidates = seg === "all" || seg === "candidates";
  const showNarrative = seg === "all" || seg === "narrative";
  const showTrends = seg === "all" || seg === "trends";

  const handlePromote = (ticker: string) => {
    setPromotedLocal((prev) => {
      const next = new Set(prev);
      next.add(ticker);
      return next;
    });
    // 永続化は X-2A の ingest アダプタ完成時に thesis_store へ繋ぐ。
    // 暫定はローカル state のみ（リロードで消える＝"気持ちのリハーサル"）。
  };

  const isPromoted = (c: Candidate): boolean =>
    !!c.promoted || promotedLocal.has(c.ticker);

  return (
    <>
      {/* 左端の縦書きトリガー */}
      <button
        type="button"
        className="zeele-trigger"
        onClick={() => setOpen(true)}
        aria-label="ZEELE 探索を開く（攻めの銘柄レコメンド）"
      >
        <span className="zeele-trigger-dot" aria-hidden />
        <span>ZEELE 攻めレコメンド</span>
      </button>

      {/* バックドロップ */}
      <div
        className={`zeele-backdrop${open ? " show" : ""}`}
        onClick={() => setOpen(false)}
        aria-hidden
      />

      {/* パネル本体 */}
      <aside
        className={`zeele-panel${open ? " show" : ""}`}
        aria-label="ZEELE 攻めの銘柄レコメンド"
      >
        <header className="zeele-header">
          <div className="zeele-header-row">
            <div className="zeele-title">
              ZEELE
              <span className="zeele-title-sub">攻めの銘柄レコメンド</span>
            </div>
            <button
              type="button"
              className="zeele-close-btn"
              onClick={() => setOpen(false)}
              aria-label="閉じる"
            >
              ✕
            </button>
          </div>
          <div className="zeele-warning">
            ⚠ 参考・未照合 ／ 決裁ラインへ直結禁止（人間が watchlist 昇格 → 翌朝 MAGI 判定）
          </div>
          <div className="zeele-segs">
            {SEGMENTS.map((s) => (
              <button
                type="button"
                key={s.key}
                className={`zeele-seg${seg === s.key ? " active" : ""}`}
                onClick={() => setSeg(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </header>

        <div className="zeele-body">
          {showCandidates && (
            <>
              <div className="zeele-section-label">攻め候補（プリセット結果）</div>
              {candidates.length === 0 ? (
                <div className="zeele-card-note">
                  候補なし（screening pipeline 未配線。次セッションで実候補を流し込みます）
                </div>
              ) : (
                candidates.map((c) => {
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
                      {c.narrative && <div className="zeele-narrative">{c.narrative}</div>}
                      <div className="zeele-meta">
                        {c.reference_score !== undefined && (
                          <span>
                            参考スコア <b>{c.reference_score}</b>
                          </span>
                        )}
                        {c.x_sentiment && <span>X：{c.x_sentiment}</span>}
                      </div>
                      <div className="zeele-actions">
                        <button
                          type="button"
                          className={`zeele-btn-promote${promoted ? " promoted" : ""}`}
                          onClick={() => !promoted && handlePromote(c.ticker)}
                          disabled={promoted}
                        >
                          {promoted ? "✓ watchlist 昇格済" : "→ watchlist 昇格"}
                        </button>
                        {promoted && (
                          <span className="zeele-card-note">翌朝バッチで MAGI 判定対象</span>
                        )}
                      </div>
                    </article>
                  );
                })
              )}
            </>
          )}

          {showNarrative && themes.length > 0 && (
            <>
              <div className="zeele-section-label">narrative テーマ</div>
              {themes.map((t, i) => (
                <article className="zeele-card" key={`narr-${i}`}>
                  <div className="zeele-card-head">
                    <span className="zeele-ticker">{t.title}</span>
                  </div>
                  {t.summary && <div className="zeele-narrative">{t.summary}</div>}
                  {t.source && <div className="zeele-card-note">出典：{t.source}</div>}
                </article>
              ))}
            </>
          )}

          {showTrends && trends.length > 0 && (
            <>
              <div className="zeele-section-label">X トレンド</div>
              {trends.map((t, i) => (
                <article className="zeele-card" key={`trend-${i}`}>
                  <div className="zeele-card-head">
                    <span className="zeele-ticker">{t.title}</span>
                  </div>
                  {t.summary && <div className="zeele-narrative">{t.summary}</div>}
                </article>
              ))}
            </>
          )}

          {/* フッタ：北極星 + 市場対象 */}
          {(data?.north_star || data?.market_focus) && (
            <div className="zeele-footer">
              {data.north_star && (
                <div>
                  <span className="key">北極星：</span>
                  <a
                    href={data.north_star.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {data.north_star.name}
                  </a>{" "}
                  「{data.north_star.mantra}」 (D-24)
                </div>
              )}
              {data.market_focus && (
                <div>
                  <span className="key">市場対象：</span>
                  {data.market_focus.primary} {data.market_focus.primary_weight_pct}% /{" "}
                  {data.market_focus.satellite} {data.market_focus.satellite_weight_pct}% (
                  {data.market_focus.decision_id})
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
