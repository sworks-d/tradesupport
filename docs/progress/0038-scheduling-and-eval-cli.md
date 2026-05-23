# 0038 定期実行（launchd）＋評価CLI

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「可能な限り自走して後で報告して」。残りの「定期実行」。

## 実施内容
- 新規 `scripts/run_evaluation.py`：評価ジョブの実行入口（P6）。`evaluate_due_decisions` を
  yfinanceネイティブ価格で回し、件数＋Track Record（命中率/平均R/平均リターン/対ベンチ超過・暫定表示）を出力。
  ※actual_return は比率＝entry/exit を同一通貨基準で揃える前提（record_entry はネイティブ記録）を明記。
- `ops/launchd/`：macOS launchd 雛形2本（朝バッチ 7:00／評価 7:30）。plistlib で well-formed 確認済。
- `docs/OPERATIONS_SCHEDULING.md`：設置手順（bootstrap/kickstart/bootout）・コスト注意（--no-quality）・
  平日のみ設定・cron代替。決裁/発注は moomoo 手動（自動化は判断生成＋評価まで）。

## 受入
- run_evaluation.py 構文OK・ruff clean。plist 2本 well-formed。全スイート green（380）。
- ※実 launchd 登録はユーザー環境作業（plist内パスは /Users/a05/tradesupport 前提）。

## architecture.html
- §0：P6-b行のハッシュ確定＋「定期実行」行。

## 状態/次（自走）
- これで `load_universe → (launchd) run_morning_batch → run_evaluation` が自動で回る土台が完成。
- 残り：XBRL深掘り（EDINET書類からGC注記/監査意見の本文検出）。
