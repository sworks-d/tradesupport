/**
 * F5 ZEELE 探索画面（skeleton）
 *
 * ダッシュボードの ZEELE ゾーンが「数週単位の熟成中・上位4件」しか出さないので、
 * 深掘り（全候補・narrative 詳細・X トレンド全件・セクター文脈）は本画面で。
 *
 * 設計（memory: zeele-magi-role-split）:
 *   - ZEELE = 攻め（参考・未照合・SCORE 数値 OK）
 *   - 決裁ラインへは "watchlist 昇格" 経由でのみ流入（人間ゲート）
 *
 * 現状はプレースホルダ（実候補配線は X-2A ingest アダプタ完成時）。
 */

import Link from "next/link";

export const metadata = {
  title: "ZEELE 探索 — 攻めの深掘り",
};

export default function ZeeleExplorePage() {
  return (
    <main className="zeele-explore">
      <header className="ze-header">
        <Link href="/" className="ze-back">
          ← ダッシュボードに戻る
        </Link>
        <div className="ze-title-row">
          <h1 className="ze-title">
            ZEELE 探索 <span className="ze-title-en">Explore · 攻め</span>
          </h1>
          <span className="ze-status">⚠ 参考・未照合 / 工事中</span>
        </div>
        <p className="ze-sub">
          ダッシュボードの ZEELE ゾーンは上位4件のみ。ここでは <b>16プリセット全候補</b>
          ・<b>narrative テーマ全件</b>・<b>X トレンド</b>・<b>セクター文脈</b> を扱う。
          実候補配線は <code>X-2A ingest アダプタ</code> 完成時。
        </p>
      </header>

      <section className="ze-section">
        <div className="ze-section-head">
          <h2>16プリセット全候補</h2>
          <span className="ze-section-meta">value / growth / momentum / contrarian / alpha / pullback / theme …</span>
        </div>
        <div className="ze-placeholder">
          <div className="ze-placeholder-title">⛏ 未配線</div>
          <p>
            screening_agent の出力履歴を週次集計し、<b>複数プリセットで連続上位入賞</b>
            した銘柄を ZEELE 候補として並べる。
          </p>
          <p>
            実装：<code>discipline/thesis_ingest.py</code>（次セッション）で
            screening_agent → snapshot.zeele.candidates の配線を作る。
          </p>
        </div>
      </section>

      <section className="ze-section">
        <div className="ze-section-head">
          <h2>narrative テーマ</h2>
          <span className="ze-section-meta">複数週もっている構造的テーマのみ</span>
        </div>
        <div className="ze-placeholder">
          <div className="ze-placeholder-title">⛏ 未配線</div>
          <p>
            Grok / TDnet / 日経 から集めたテーマを、<b>持続性</b>（週次の出現頻度）で
            フィルタ。一過性のスポット報道は除外する。
          </p>
        </div>
      </section>

      <section className="ze-section">
        <div className="ze-section-head">
          <h2>X トレンド</h2>
          <span className="ze-section-meta">XAI_API_KEY 設定時に有効</span>
        </div>
        <div className="ze-placeholder">
          <div className="ze-placeholder-title">⛏ 未配線</div>
          <p>
            Grok API でハッシュタグ・銘柄言及を集計。<b>センチメント変化</b>
            （ポジ/ネガの推移）を可視化する。
          </p>
        </div>
      </section>

      <section className="ze-section">
        <div className="ze-section-head">
          <h2>セクター文脈</h2>
          <span className="ze-section-meta">JP 主軸 + US ETF サテライト（D-25）</span>
        </div>
        <div className="ze-placeholder">
          <div className="ze-placeholder-title">⛏ 未配線</div>
          <p>
            TOPIX セクター別の相対強度・US ETF（QQQ/VOO/SOXX）の動きを並べて、
            「今の市場でどのセクターが熱いか」の俯瞰を出す。
          </p>
        </div>
      </section>

      <footer className="ze-footer">
        <p>
          北極星: <a href="https://github.com/tradermonty/claude-trading-skills" target="_blank" rel="noopener noreferrer">
            claude-trading-skills
          </a>「Plan → Trade → Record → Review → Improve」(D-24)
        </p>
        <p>市場対象: <b>JP 90%</b> / US ETF 10% (D-25)</p>
      </footer>
    </main>
  );
}
