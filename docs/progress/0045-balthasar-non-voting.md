# 0045 — 心臓：偽の確信度を除去（BALTHASARを投票外に）

## ユーザーが与えた指示
- 心臓「偽の確信度を除去」：BALTHASARの無エッジ票をMAGI合意・確信度から外す（事実の摘出には残してよい）。
- 理由：コイン投げ票が「全会一致＝高確信」を水増しする。検証がノイズを検証している。

## 実施内容
- 新規 `trading_agent/magi/policy.py`：合意ポリシーを単一の正に。`VOTING_JUDGES=("MELCHIOR","CASPER")`／`NON_VOTING_JUDGES=("BALTHASAR",)`／`voting(verdicts)`。
  - 根拠：BALTHASAR（株価/テクニカル）は液体大型でエントリー予測力ゼロ（勝率≈50%）と実測（offense-edge スカウト）。方向票は数えない。
- `magi/integration.py::classify_split`：合意・確信度は**投票2審判のみ**で算定。`agree_count`/`total` は業績・文脈ベース（株価は「参考・投票外」として併記）。割れ類型を「業績◯文脈✕／文脈◯業績✕」に刷新。内在不安(B-5)は全審判の反証を数える（BALTHASARの事実も使う）。
- `magi/commander.py::command`：unanimous_buy/buys/dissent/na_judges を投票審判で算定。反証集約(_aggregate_counters)は全審判（BALTHASARの過熱等の事実を残す）。
- `magi/defense.py::verify`：既定保留ゲートの `unanimous_buy`/`has_na` を投票審判で判定（BALTHASARの票で水増し/過剰ブロックしない）。出典・時点照合は全actionableに保守的に維持。
- `magi/persist.py::derive_gendo_stance`：碇の構えを投票審判で算定。

## 検証
- `test_integration_commander.py` を新セマンティクスで全面書換／`test_defense.py` に「BALTHASARの票はゲート外」テスト追加。
- defense/persist の既存テストは voting-only でも結果不変＝そのままパス。
- ruff All passed・mypy(magi) clean（persist.py の `list[dict]` 2件は本変更外の既存warn・MORNING_REVIEW記録）・tests/ 全 green。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：心臓「偽確信度除去」完了。BALTHASARは「事実摘出（反証）専任」になり、合意・確信度・既定保留は業績×文脈に純化。
- 次：スクリーニングを検品に純化（予測ランキング除去）→構造（逓減・Core-Satellite）→ペーパー執行。
