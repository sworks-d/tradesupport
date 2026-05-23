# 0027 S7a（弾）— V字（ターンアラウンド）質判定

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「進めて。」（推奨の S7 V字スクリーニング精緻化へ）。

## 判断（研究 領域3-A の核心）
V字＝「底にいる」でなく「**底から反転の点火**」。最重要指標は **earnings acceleration（成長率の加速＝二階微分）**。
**Value×Momentum両立**（Asness）＝点火（②）と株価転換（③）のAND。**底だけ＝value trap として除外**。

## 実施内容
- `screening/financials.py`：`Financials.prior2`（3期目）を追加（earnings acceleration に必要）。
  `has_three_periods()`／`fetch_financials` が3期目も取得（_fetch_yfinance_statements は既に3列取得）。
- 新規 `screening/turnaround.py::assess_turnaround(fin, signals, credibility)` → `TurnaroundResult`：
  - ①業績の底（営業マージン<5%/赤字）②反転の点火（**3期で成長率加速**、無ければマージンYoY改善で代替）
    ③株価底打ち転換（BALTHASARシグナルで近似：GC在/DC不在＝Stage2）④生存性（信用性 Z/F が risk でない）。
  - 判定：①〜④揃い＝**v_candidate**／①だが②欠＝**value_trap**（底にいるだけ）／①無し＝**not_applicable**／欠損＝na。
  - 全てコード（R1）・欠損na（R4）。Value×Momentum＝②と③のAND。生存性は領域1（S5）と統合。
- テスト `tests/unit/test_turnaround.py` 7（対象外/底だけvalue trap/4軸揃いv候補/3期加速/株価未転換/生存性riskで除外/欠損na）。

## 実測（ライブ）
- NVDA/7203＝`not_applicable`（高マージン＝底でない）。**V字スクリーンは momentum leader を拾わず、
  真のターンアラウンドだけを候補化**＝正しい挙動。

## 留意（残り＝S7の続き）
- 本コミットは**V字の質判定モジュール**。`mcp_tools/screening.py` の V字4軸/composite への統合、
  および **テーマ4軸（相対力＝対S&P 3-6ヶ月・Improving象限）** は別途（テーマは指数/構成銘柄の価格データが要る）。
  株価Stage1/2は当面 BALTHASAR シグナルで近似（将来 MA/52週安値比で精緻化）。

## architecture.html
- §0 コミットログに「S7a（弾）」行（UI反映のハッシュ確定表記）。

## コミット
- 本md＋screening/financials.py＋screening/turnaround.py＋screening/__init__＋test_turnaround.py＋
  architecture.html＋progress/README を同一コミット。

## 状態/次
- 残り：①screening.py への V字4軸統合 ②テーマ相対力（4象限・Improving）③S4b EDINET（要キー）
  ④P6評価（R-mult記録）⑤dashboard で counter 描画（UI変更＝要ゲート）。
