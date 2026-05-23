# 0037 P6-b — 評価ジョブ（評価期日到来分を採点）＋decisionにentry/stop記録

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「可能な限り自走して後で報告して」。P6 を評価ジョブまで。

## 実施内容
- `models/decisions.py`：`entry_price`／`stop_pct`／`benchmark_return` を追加（nullable・R-multiple/超過の前提）。
- `evaluation/job.py`：
  - `record_entry(engine, id, entry_price, stop_pct, target_return, target_period_days)`：発注時に
    entry/stop/target/評価期日（=base+期間）を刻む。status approved/order_listed→ordered。
  - `evaluate_due_decisions(engine, price_lookup, benchmark_lookup=None, today=None)`：
    **評価期日到来かつ未評価（pending）かつentry記録済**の decision を実価格で採点
    （actual_return/hit-miss/benchmark）→ 永続化。**先読みしない**（期日前は据え置き）・**冪等**（評価済は再評価しない）。
    返り値＝(今回件数, 全評価済みTrack Record)。価格lookupは注入（テスト可能）。
- テスト `tests/unit/test_evaluation_job.py` 8（record_entry刻み・期日到来で評価・期日前は据え置き・stopでmiss・
  entry無しskip・冪等・ベンチ超過）。

## 受入
- 全スイート green（380）。touched ファイル ruff clean。

## architecture.html
- §0：P6-a行のハッシュ確定＋「P6-b 評価ジョブ」行。

## 状態/次（自走）
- 次：定期実行（朝バッチのスケジューラ入口）→ XBRL深掘り。Track Record の snapshot/UI 反映は後続（D-19注意）。
- 実運用：発注確定時に record_entry を呼ぶ配線（決裁はmoomoo手動なので、約定後にentryを記録する運用）。
