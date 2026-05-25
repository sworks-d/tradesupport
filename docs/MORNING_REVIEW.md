# 朝の確認（自走セッション 2026-05-24夜 → 翌朝）

指示：「ペーパー運用まで100%。全タスク化して朝までに自走完了」＋優先順位リスト（即効/心臓/構造/後回し）。
自走方針（[[autonomous-overnight-workflow]]）に従い、**判断が要る基盤項目はここに設計を集約**し、コードを推測で確定しない。
実施詳細は `docs/progress/0044〜0047`、数値レポートは `docs/reports/GENDO_v001.md`。

---

## ✅ 完了（4コミット・全て ruff/tests green）
| コミット | 区分 | 内容 | 朝の確認ポイント |
|---|---|---|---|
| 0044 | 即効 | **利確キャップ撤廃(B')**＝固定stop＋target_date(time_exit)・勝ち放任／**目標是正**(+30〜50%破棄→リスク調整後パッシブ超え＋生存) | `sell_recommender.py` の出口が意図通りか。実測：利確キャップが収益の約2/3を破壊→撤廃で+132%→+406%(production BT) |
| 0045 | 心臓 | **BALTHASARを投票外**＝株価のコイン投げ票を合意/確信度/既定保留から除外（反証・価格注記は残す） | `magi/policy.py` の VOTING_JUDGES=(MELCHIOR,CASPER) で良いか |
| 0046 | 構造 | **逓減スケジュール**(口座→risk%↓/銘柄数↑)＋**Core-Satellite比率**(0.85/0.15) を params.py/G-0 | 逓減の採用値（下記「下した決定」）でよいか |
| 0047 | 検証 | **ポートフォリオBT(NaN修正)＋予算別ロールアウト**＋`docs/reports/GENDO_v001.md` 定型レポート | 逓減でSharpe1.16→1.53/DD−25→−17%。だしbuy&holdに大敗(αなし)＝規律の価値はDD半減 |

---

## 🔴 判断が要る論点（=ペーパー運用100%の残り。推奨を先頭に）

### 論点1：B1 スクリーニングを「検品」に純化（**選抜セマンティクス変更＝基盤**）
現状：V字/テーマの**予測**composite で上位選抜（＝「当てる選定」）。哲学（[[tool-purpose-buy-and-hold]]）と衝突。
- **推奨**：選抜軸を「予測composite上位」→**「検品(除外)の生存者」**へ。`screening_passed` = value_trap でも credibility(warn) でもない生存。予測スコア(V字/テーマ)は**サテライト信号**に降格。生存者は時価総額順（分散・流動性）。
- 判断点：除外をどこまで厳しく？ **推奨＝value_trap＋credibility(warn) を除外／健全性は財務欠損時は除外しない(R4)**。
- なぜ朝送り：選抜の意味を変える基盤で、誤ると Core 配分(論点3)が崩れる。既存 `test_screening.py`/`morning_batch` 候補選定に波及。
- 影響：`mcp_tools/screening.py`・`test_screening.py`・`orchestrator/morning_batch.py`。**承認あれば即実装可（半日）**。

### 論点2：ペーパー執行ループ（執行→保有→評価の配線）
現状：decision は awaiting/approved まで。Portfolio 化の自動フックなし（StandIn は ¥100k/0保有の静的fallback）。
- **推奨**：`approved` decision → `recommend_position`(逓減params) → 現在価格で**紙約定** → `Portfolio(active)` 化 → `record_entry`。実ブローカー不要。
- 判断点A（決裁は人間=原則2/5）：紙でも**「approvedのみ執行」を推奨**。autopilot(推し&非保留を自動approve)は**明示トグル**で別途（既定OFF）。
- 判断点B（紙の現金台帳）：Portfolio に cash 列なし。**推奨＝PortfolioSnapshot に paper cash を持たせる**（or 専用 paper_ledger）。
- 判断点C：Portfolio 行に要る `target_date/target_pct/stop_loss_pct` を decision＋サイジングから導出（既定 stop12%・target_period から target_date）。
- 影響：新規 `portfolio/paper_exec.py`＋models微修正＋テスト。**承認あれば即実装可（1日）**。

### 論点3：Core-Satellite 配分の実配線（params は実装済・配線が残り）
- **推奨**：コア(85%)＝**検品生存の質分散塊**へ機械的配分（積立・勝ち放任）／サテライト(15%)＝**スクリーニング予測信号**へ小口・損切り固定。**論点1の確定が前提**。

### 論点4：ペーパー運用エントリーポイント（一気通貫）
- **推奨**：`scripts/run_paper.py`＝universe→収集→**検品**→MAGI→提案→[承認]→**紙執行**→評価 を一本化。**論点1・2の確定後**。

> ペーパー運用100%＝論点1〜4の確定＋実装。承認は§形式で1〜4に○/×＋メモを返してくれれば、その順で実装する。

---

## 自走中に下した決定（事後承認 or 巻き戻し可）
- B2：BALTHASAR を「投票外（事実摘出＝反証専任）」に。`VOTING_JUDGES=(MELCHIOR,CASPER)`。
- 逓減採用値：境界 30万/100万/300万/1000万、risk 2.0→1.5→1.2→1.0→0.8%、枠 5→8→12→16→20、現金下限 20→20→15→15→10%（要実績で更新）。
- Core/Satellite = 0.85/0.15。
- portfolio BT のエントリは GC 固定（αの測定でなく「規律と出口の予算別挙動」を見る目的のため）。
- architecture.html §0：コミット毎更新の慣行に対し、本夜は4コミット分を**一括追記**（大HTMLの逐次編集リスク回避）。

## 既知の小事項
- mypy：`trading_agent/magi/persist.py:238,253` に**既存**の `list[dict]` 型引数 warn（本夜の変更外。2文字で解消可だが範囲外のため未着手）。

## 未着手・後回し（指示通り）
- **PIT財務で質エッジ検証**＝重い・最後。ペーパー運用100%には不要。やるなら無料路線＝EDGAR/EDINET(提出日キー)＋GDELT の履歴ストア構築（別プロジェクト規模）。
