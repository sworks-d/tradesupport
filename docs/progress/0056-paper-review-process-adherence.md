# 0056 — P4-4 評価の向け直し（守りはリターンでなくプロセス遵守で測る）

## ユーザーが与えた指示
- 「進めて」＝推奨パス継続。P4-4：守りはリターンで測らない→質パッシブ追随＋プロセス遵守で測る。

## 実施内容
- `trading_agent/evaluation/paper_review.py`：
  - `check_process_adherence()`＝現紙ポートフォリオの**規律点検**（保有数≤上限／**利確で刻まない(B')**＝profit_takingシグナルの不在／現金下限維持／1銘柄上限以下）。守りの本体＝プロセス遵守を点検（取得原価近似）。
  - `sharpe()/max_drawdown()/risk_adjusted_vs_passive()`＝履歴が貯まったら「コア vs パッシブ」をリスク調整で比較する純関数。
- `scripts/run_paper.py --evaluate`：**プロセス遵守を主**に表示し、命中率/平均Rは「副次（払戻比の産物になり得る＝過信しない）」に格下げ。守りは数ヶ月のリターンに出ない＝正常、と明記。

## 検証
- `tests/unit/test_paper_review.py`（8件）：規律遵守=全OK／利確シグナル痕跡=違反／現金下限割れ=違反／1銘柄超過=違反／sharpe・max_drawdown・excess。
- ruff/mypy clean・tests/unit 全 green・run_paper も clean。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：ペーパー評価が「守り＝プロセス遵守／攻め=較正前」の正しい測り方に向いた。
- 次（データ/設計待ちで適切に保留）：攻め昇格条件の実装は**前向き紙データが貯まってから**（今は offense枠0 が pre-promotion 状態）／sleeve永続化は**サテライト発生後**／専用カードUIは**デザイン確認後**。
