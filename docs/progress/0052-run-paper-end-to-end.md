# 0052 — P4-3 ペーパー運用 一気通貫（operator_view＋run_paper）

## ユーザーが与えた指示
- テストまで推奨で実装。推奨順＝run_paper 一気通貫を先に通して end-to-end を動かす。決裁は人間・攻めは情報のみ。

## 実施内容
- `trading_agent/portfolio/operator_view.py`（テスト可能コア）：`operator_cards()`＝awaiting decision の永続MAGI4表(JudgeVerdict/Verification/SplitPattern/CommanderRec)を読み、規律層サイジングを当てて GENDO 推奨カードを組む（ネット非依存・注入）。`render_card()` で1銘柄1カード文字列。
- `scripts/run_paper.py`（オーケストレーション）：
  - 既定＝**監視**：朝バッチ(run_morning_batch)→GENDO推奨カード提示（決裁待ち）。
  - `--fill`＝承認(approved)分を**翌寄り**で紙約定（月次DCA想定・paper_fill_approved）。
  - `--evaluate`＝評価期日到来分を実価格で採点。
  - 価格は universe市場でJPY換算(US×USDJPY)、現金は StandIn。決裁は人間（自動承認しない）。

## 検証
- `tests/unit/test_operator_view.py`（4件）：揃い→コア積み増し／信用性warn→見送り／カード描画／awaiting以外は無し。
- run_paper：ruff/mypy clean・import smoke OK（ネットmain は実データ実行で確認）。
- tests/unit 全 green。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：**監視→GENDO推奨カード→[決裁]→紙約定→評価 が end-to-end で繋がった**（P4-3）。攻めは情報のみ（枠0）。
- 次：P4-4 評価の向け直し（守りはリターンでなく質パッシブ追随＋プロセス遵守）／攻め昇格条件＝前向き較正＋コントロール参戦。architecture 反映。
