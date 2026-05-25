# 0050 — P4-1 ペーパー執行ループ（守り主導）＋ロードマップ改訂

## ユーザーが与えた指示
- 攻め=最適化／守り=検証、攻めはチャンピオンを作らず昇格制（コントロール参戦で勝った時のみ）、守りは論理固定＋ペーパーでフル稼働。歴史蠱毒(P2)はクリティカルパス外。最小路線＝P0(済)→P4紙ループ(守り主導)から着手。紙約定=翌寄り／コア積立=月次DCA。

## 実施内容
- `docs/plan/ROADMAP_kodoku_to_paper.md`：改訂版ロードマップ（攻め昇格条件＝30件＋コントロール参戦＋差の持続／守りはリターンで測らない＝自爆せず質パッシブ追随＋プロセス遵守／会計分離①システム攻め0円②裁量サテライト死ぬサイズだが前向き記録）。
- `trading_agent/portfolio/paper_exec.py`：`paper_fill_approved()`＝**status=approved のみ**を**翌寄り価格**で紙約定→`Portfolio(active)`化→`record_entry`。決裁は人間（自動承認しない）。サイジングは規律層`recommend_position`。出口はB'（target_pct=0＝利確で刻まない／stop_loss_pct=-stop／target_date=+120d）。v1はqty整数（US端株は丸め＝正直な制約）。

## 検証
- `tests/unit/test_paper_exec.py`（4件）：approved執行・approved以外は不執行・価格不可skip・高単価skip。
- tests/unit 全 green・ruff All passed・mypy clean。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：P4-1完了。承認済decisionが紙約定→保有→評価前提まで通る。
- 次：P4-2 Core-Satellite配分（コア=守り検品生存）／P4-3 run_paper一気通貫（監視日次・積立月次）／P4-4 評価向け直し。
