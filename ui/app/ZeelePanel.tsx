"use client";

/**
 * ZEELE Zone — 攻めの銘柄レコメンド（売り/買いの左に常時表示）
 *
 * 設計（memory: zeele-magi-role-split）:
 *   - ZEELE = 攻めの銘柄レコメンド（新規買い候補・narrative・edge候補）
 *   - MAGI = 守り（保有規律・posture・売り判断）
 *
 * UI:
 *   - dashboard.html の .action-zone（3カラム化済）の最左にマウント
 *   - 売り/買いと同じ .action-card 形状で、紫アクセント+破線左ボーダーで「参考」を表現
 *   - 既存 zone-head / zone-title スタイルを流用して視覚的に整合
 *
 * データ:
 *   - /data/snapshot.json の `zeele.candidates[]` を読む
 *   - 各候補に [→ watchlist 昇格] ボタン（永続化は X-2A ingest 完成時に thesis_store へ接続）
 *
 * 制約:
 *   - 参考スコアは「未照合」として表示・MAGI 決裁ラインに流さない
 *   - watchlist 昇格 → 翌朝バッチで MAGI 判定（人間ゲートが唯一の橋）
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Candidate = {
  ticker: string;
  name?: string;
  preset?: string;
  narrative?: string;
  reference_score?: number;
  x_sentiment?: string;
  promoted?: boolean;
};

type ZeeleData = {
  candidates?: Candidate[];
  generated_at?: string;
};

type Snapshot = {
  zeele?: ZeeleData;
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

export default function ZeelePanel() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [mount, setMount] = useState<HTMLElement | null>(null);
  const [promotedLocal, setPromotedLocal] = useState<Set<string>>(new Set());

  // dashboard.html の中の #zeele-zone-mount を React の Portal ターゲットに使う。
  // dangerouslySetInnerHTML 後の DOM を捕まえるため、ポーリングで待つ。
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
      if (attempts++ < 30) {
        requestAnimationFrame(tick);
      }
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
      .catch(() => {
        // snapshot 不在は静かに空表示
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!mount) return null;

  const zeele = data?.zeele;
  const candidates = zeele?.candidates ?? [];

  const handlePromote = (ticker: string) => {
    setPromotedLocal((prev) => {
      const next = new Set(prev);
      next.add(ticker);
      return next;
    });
    // 永続化は X-2A ingest 完成時に thesis_store へ接続。
    // 暫定はローカル state（リロードで消える＝"気持ちのリハーサル"）。
  };

  const isPromoted = (c: Candidate): boolean =>
    !!c.promoted || promotedLocal.has(c.ticker);

  const visible = candidates.slice(0, 4); // ゾーン幅に合わせ上位 4 件

  return createPortal(
    <>
      <div className="zone-head">
        <div className="zone-title">
          ZEELE <span className="en">Explore</span>
        </div>
        <div className="zone-count">
          <strong>{candidates.length}</strong>件 候補
        </div>
      </div>
      <div className="zeele-warning">⚠ 参考・未照合 ／ 決裁ライン直結禁止</div>

      <div className="zeele-body">
        {visible.length === 0 ? (
          <div className="zeele-card-note">
            候補なし（screening pipeline → ZEELE の配線は次セッション）
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
                {c.narrative && <div className="zeele-narrative">{c.narrative}</div>}
                <div className="zeele-meta">
                  {c.reference_score !== undefined && (
                    <span>
                      参考 <b>{c.reference_score}</b>
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
                    {promoted ? "✓ 昇格済" : "→ watchlist 昇格"}
                  </button>
                </div>
              </article>
            );
          })
        )}
        {candidates.length > visible.length && (
          <div className="zeele-card-note" style={{ marginTop: 8 }}>
            ほか {candidates.length - visible.length} 件 ／ 詳細探索は F5（後続）
          </div>
        )}
      </div>
    </>,
    mount,
  );
}
