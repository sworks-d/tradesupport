# ハルシネーション防壁 正本表

**作成日**: 2026-06-01
**Phase**: 0 設計合意（実装前ドキュメント整備）
**目的**: 実装に散在する番号付き防壁（H / G / S / C 系）を網羅し、PIPELINE v3 各ノードでの効きをマッピングする。

---

## 0. 背景

[docs/SYSTEM_PURPOSE.md:79](SYSTEM_PURPOSE.md#L79) に「ハルシネーション対策（11 防壁）」と記載されているが、本文には 4 項目しか列挙されていない（網羅表が文書化されていない）。

実装側には番号付き防壁が `trading_agent/` 配下に散在しており、本ドキュメントは実装から抽出した正本表として整備する。

---

## 1. 防壁カテゴリ（4 系統）

| カテゴリ | プレフィックス | 概念 |
|---|---|---|
| **H 系** | H1, H2, H-6, H-7 等 | ハルシネーション抑止（LLM 出力検証・データ不足時の挙動） |
| **G 系** | G-0〜G-8 | 規律ゲート（リスク管理・サイジング・DD） |
| **S 系** | S4〜S7 | スクリーニング層（信用性・財務・V字・相対力） |
| **C 系** | C1, C2 等 | 完全性照合（Universe 整合・仮想 ticker 弾き） |
| **市場ガード** | （無番号） | 日経/TOPIX 暴落時の停止 |

---

## 2. 防壁網羅表（実装から抽出）

### H 系：ハルシネーション抑止

| # | 名前 | 場所 | 内容 | 効くノード |
|---|---|---|---|---|
| **H1** | データ不足で推測しない | [wille/ritsuko.py:752](../trading_agent/wille/ritsuko.py#L752) | データ取れない銘柄は None 返却。Brief 内 score 算出で「無いものは無い」と返す | AKAGI Brief（katsuragi_dispatch 内）|
| **H2** | LLM 出力値域検証 | [wille/ritsuko.py:1315](../trading_agent/wille/ritsuko.py#L1315) | Haiku 出力を CASPER LLM パターンで値域検証。範囲外は無効化 | topics_collector / news_sentiment / AKAGI Brief |
| **H2'** | LLM ハルシネーション禁止プロンプト | [agents/topics_collector.py:285-290](../trading_agent/agents/topics_collector.py#L285) | Haiku に「与えられた材料以外の事実・数値・将来予測を生成しない」と明示 | topics_collector |
| **H3** | preset データ品質 confidence 反映 | [portfolio/ds_scout.py:174,212](../trading_agent/portfolio/ds_scout.py#L174) | preset 取得状況を明示分岐、不明時は 10% ディスカウント（v2.8 で 0.5 偽装を廃止） | DS scout（katsuragi_dispatch 内）|
| **H-6** | 保有との相関で fill 抑制 | [portfolio/correlation.py:185](../trading_agent/portfolio/correlation.py#L185), [morning_batch.py:433](../trading_agent/orchestrator/morning_batch.py#L433) | 保有銘柄と相関 \|r\| ≥ 0.7 の新規 buy を skipped 化（ピラミッディングは対象外） | auto_fill（auto モード）|
| **H-7** | ポートフォリオ DD ブレーキ | [portfolio/anomaly_detector.py:45,296](../trading_agent/portfolio/anomaly_detector.py#L45) | 累計 DD ≤ -10% で新規 buy 抑制。default threshold = -0.10 | auto_fill / anomaly_check |

### G 系：規律ゲート

| # | 名前 | 場所 | 内容 | 効くノード |
|---|---|---|---|---|
| **G-0** | 確定パラメータ | [trading_agent/risk/params.py](../trading_agent/risk/params.py) | HALT / 上限クランプの確定値。`docs/plan/spec/G_risk_discipline.md` で正本管理 | anomaly_check / katsuragi_dispatch |
| **G-1** | R-mult サイジング | [portfolio/sizing.py:1](../trading_agent/portfolio/sizing.py#L1), [morning_batch.py:700](../trading_agent/orchestrator/morning_batch.py#L700) | stop 逆算で position size を決定 | pyramid_check / katsuragi_dispatch |
| **G-2** | trailing × earnings_guard 合成 | [portfolio/trailing_check.py:143](../trading_agent/portfolio/trailing_check.py#L143) | 決算前 trailing stop を厳格化 | trailing_check |
| **G-3** | Universe 照合（fill 出口） | [portfolio/paper_exec.py:584](../trading_agent/portfolio/paper_exec.py#L584) | fill 直前に Universe.is_active を再照合。ハルシネーション完全性の最終防壁 | auto_fill / paper_close_due |
| **G-4** | 発注リスト R-mult / stop / 1R 損失併記 | [reporting/order_list.py](../trading_agent/reporting/order_list.py) | 発注 HTML に R-mult サイズ、stop 価格、想定損失（1R）を併記 | notify |
| **G-5** | （未確認・予約番号） | - | - | - |
| **G-6** | （未確認・予約番号） | - | - | - |
| **G-7** | 逓減スケジュール | [risk/params.py:75](../trading_agent/risk/params.py#L75) | 口座サイズ → risk% / 銘柄数の表。少額時は集中、大額時は分散 | katsuragi_dispatch（Treasury → 予算）|
| **G-8** | Core-Satellite 比率 | risk/params.py | 0.85 / 0.15 でコア・サテライト分配 | katsuragi_dispatch |

### S 系：スクリーニング層

| # | 名前 | 場所 | 内容 | 効くノード |
|---|---|---|---|---|
| **S4** | 2 期分の財務（餌） | [screening/financials.py:1](../trading_agent/screening/financials.py#L1) | Beneish M-Score / Piotroski F-Score / Altman Z-Score を 2 期分財務から計算 | screening |
| **S4b** | 開示レッドフラグ | [screening/edinet_xbrl.py:1](../trading_agent/screening/edinet_xbrl.py#L1), [magi/persist.py:229,243](../trading_agent/magi/persist.py#L229) | EDINET / TDnet で GC 注記・監査意見・訂正有報を検出（D-14 第 1 フィルタ） | screening / magi_verify |
| **S5** | 信用性フィルタ | [screening/credibility.py:1](../trading_agent/screening/credibility.py#L1) | M/F/Z スコアで credibility_flag = ok / warn を判定。warn は composite を 30% 減点 | screening / magi_verify（防御層） |
| **S5b/S6** | MELCHIOR 反証（信用性赤・利益の質） | [screening/credibility.py:283,304](../trading_agent/screening/credibility.py#L283) | 信用性危険域を MELCHIOR の自領域反証に摘出。利益の質（accrual / CFO<純利益 / DSO 悪化 / 在庫増）の赤を 2 期財務からコード摘出 | magi_verify |
| **S7** | V 字（ターンアラウンド） | [screening/turnaround.py:1](../trading_agent/screening/turnaround.py#L1) | 4 軸判定：①業績の底 ②反転の点火 ③株価底打ち ④生存性。①〜④揃いで v_candidate、①あり②欠で value_trap | screening |
| **S7a** | V字 Value × Momentum 適用 | screening composite | 点火なしの底は満額にせず value_trap フラグ。底だけは買わない | screening |
| **S7c** | 相対力 + 4 象限 | [screening/relative_strength.py:1](../trading_agent/screening/relative_strength.py#L1) | 対市場の相対力 4 象限（Leading / Weakening / Lagging / Improving）。Improving = テーマ V 字。市場 proxy = ^GSPC / ^N225 | screening |

### C 系：完全性照合

| # | 名前 | 場所 | 内容 | 効くノード |
|---|---|---|---|---|
| **C1** | 上場廃止銘柄除外 | [orchestrator/morning_batch.py:80-95,90](../trading_agent/orchestrator/morning_batch.py#L80) | `Universe.is_active=True` に限定して候補抽出。8729 上場廃止銘柄の Universe 復活は禁じ手 | screening / materialize / katsuragi_dispatch |
| **C2** | 仮想 ticker 弾き（screening 出口） | [agents/screening_agent.py:170-172](../trading_agent/agents/screening_agent.py#L170) | screening tool が外部経路で予期しない ticker を返しても、Universe.is_active=True に無いものは弾く | screening |
| **C3** | 仮想 ticker 弾き（fill 出口） | [portfolio/paper_exec.py:257-270](../trading_agent/portfolio/paper_exec.py#L257) | fill 直前の Universe 最終照合。出口の防壁 | auto_fill / paper_close_due |

### 市場ガード

| # | 名前 | 場所 | 内容 | 効くノード |
|---|---|---|---|---|
| **MG-1** | 日経/TOPIX -3% で新規 fill 停止 | [portfolio/misato.py:1004-1020](../trading_agent/portfolio/misato.py#L1004) | 市場全体の暴落時に新規 fill をスキップ。既存保有の close は走る | katsuragi_dispatch |
| **MG-2** | earnings_guard | [portfolio/earnings_guard.py](../trading_agent/portfolio/earnings_guard.py) | 決算前後の取引制限 | sell_recommender / trailing_check |
| **MG-3** | theme_strength | [portfolio/theme_strength.py](../trading_agent/portfolio/theme_strength.py) | テーマ強度ガード | katsuragi_dispatch |
| **MG-4** | opportunity_fill 4 ガードレール | [wille/opportunity_fill.py:33-43](../trading_agent/wille/opportunity_fill.py#L33) | 1 銘柄 / source / sector / 機 上限。greedy fill の集中リスク抑制 | katsuragi_dispatch |

---

## 3. パイプライン v3 各ノードでの防壁マッピング

[docs/PIPELINE_v3.md](PIPELINE_v3.md) の各ノードで効く防壁：

| ノード | 効く防壁 |
|---|---|
| `pre_check` | G-0（HALT 確認） |
| `anomaly_check` | H-7（DD ブレーキ）、G-0 |
| `universe_refresh` | C1 前段（is_active 更新） |
| `topics_collector` | H2'（プロンプト明示）、H2（値域検証） |
| `screening` | S4 / S4b / S5 / S5b/S6 / S7 / S7a / S7c、C2（Universe 最終照合） |
| `zeele_curator` | C1（is_active 照合） |
| `materialize_decisions` | C1（is_active 限定） |
| `magi_verify` | S5（防御層）、S4b（開示レッドフラグ）、S6（MELCHIOR 反証）、H2（CASPER 値域） |
| `katsuragi_dispatch` | H1（データ不足 None）、H3（preset confidence 反映）、G-0、G-1、G-7、G-8、MG-1（市場ガード）、MG-3、MG-4、C1 |
| `sell_recommender` | MG-2（earnings ガード） |
| `trailing_check` | G-2（合成）、MG-2 |
| `close_due` | G-3（fill 出口照合） |
| `pyramid_check` | G-1、H-6 対象外明示 |
| `auto_fill` | H-6（相関抑制）、H-7、G-3、C3 |
| `persist_decisions` | C1 |
| `notify` | G-4（R-mult / stop / 1R 併記） |

---

## 4. 「11 防壁」の SYSTEM_PURPOSE.md 記述との対応

[docs/SYSTEM_PURPOSE.md:79-84](SYSTEM_PURPOSE.md#L79) の 4 項目：

| SYSTEM_PURPOSE 記述 | 対応する番号付き防壁 |
|---|---|
| データが取れない銘柄は判断しない（推測しない） | H1 |
| 上場廃止銘柄は Universe.is_active=False で買い候補から除外 | C1 / C2 / C3 |
| 仮想 ticker / EDINET 不在 / 信用性 warn 等で MAGI が弾く | C2 / S4b / S5 / S6 |
| 「予算枠にぎりぎり収まる候補」だけ通す経済的防壁 | G-0 / G-7 / G-8 / MG-4 |

実装には上記以外の防壁も多数あり（H2, H3, H-6, H-7, G-1〜G-4, S7, MG-1〜MG-3）、本ドキュメントが網羅版の正本。

---

## 5. 未確認 / 予約番号

| 番号 | 状態 |
|---|---|
| G-5 | 未確認（grep ヒット 0、予約番号と推定） |
| G-6 | 未確認（同上） |
| H-8 〜 H-11 | 未確認（grep ヒット 0、予約番号と推定） |

これらは将来の拡張番号と推測。実装時に番号付与する。

---

## 6. 更新方針

- **新規防壁追加時**: 本表に追記、PIPELINE v3 のマッピングも更新
- **既存防壁の閾値変更時**: 場所欄の引用先 + 本表両方を更新
- **削除時**: 廃止理由とともに本表に「削除」マーク（履歴保持）

---

## 7. 参照

- [docs/SYSTEM_PURPOSE.md](SYSTEM_PURPOSE.md#L79) — 11 防壁の概念
- [docs/PIPELINE_v3.md](PIPELINE_v3.md) — 新朝バッチ設計（防壁マッピング元）
- [docs/plan/spec/G_risk_discipline.md](plan/spec/G_risk_discipline.md) — G 系規律ゲート正本
- [docs/research/RESEARCH_METHODS.md](research/RESEARCH_METHODS.md) — S 系研究背景
- [docs/research/RISK_EXOSKELETON.md](research/RISK_EXOSKELETON.md) — 規律層設計思想
