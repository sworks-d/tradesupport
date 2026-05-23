# 0036 P6-a — 評価メトリクス（R-multiple / hit-miss / Track Record）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「可能な限り自走して後で報告して」（自走モード）。残りロードマップを順に。本コミットは P6（測って上げる）の中核。

## 実施内容（研究 領域4）
- 新規 `trading_agent/evaluation/`（metrics.py）：
  - `evaluate_position(entry, exit, target_return, stop_pct, benchmark_return=)` → `EvalResult`
    （actual_return／**R-multiple＝実リターン÷stop%**＝1Rの何倍／hit-miss-neutral／対ベンチ超過）。
    stop到達=miss・target到達=hit・中間=neutral。
  - `build_track_record(results)` → `TrackRecord`（命中率＝hit/(hit+miss)・neutral除外／平均R／平均リターン／
    平均超過／`provisional`＝最低サンプル30未満は暫定）。
  - **バイアス回避（領域4）**：評価は評価期日の実価格で（先読みしない）・少数好成績を確定扱いしない（provisional）。
    全てコード（R1・LLM非関与）。
- テスト `tests/unit/test_evaluation.py` 11（hit/miss/neutral・R-mult・超過・不正entry・命中率がneutral除外・
  平均・暫定閾値・空）。

## 受入
- 全スイート green（373）。touched ファイル ruff clean。

## architecture.html
- §0：朝バッチCLI行のハッシュ確定＋「P6-a 評価核」行。P6バッジを 🟡（評価核済）に。

## コミット
- 本md＋evaluation/(metrics.py,__init__)＋test_evaluation.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 次（P6-b）：decision に entry_price/stop_price を記録（発注時）し、評価期日到来分を実価格で採点する
  評価ジョブ（価格lookup注入）。Track Record を snapshot/UI へ（スコア相関は出さない＝D-06/D-19）。
