# 0035 朝バッチ実行 CLI（実運用の入口）＋弾ON配線

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で進めて。」（EDINET稼働後の推奨）。

## 判断（最大の穴：入口が無い）
`run_morning_batch` は**定義・テスト済だが、どこからも呼ばれていなかった**（CLI/main無し）。
＝パイプライン全体を**実行する手段が無い**＝実運用できない。これが最優先の穴。
推奨②「朝バッチで品質ON」も、まず実行入口が要る。→ 実行CLIを作り、弾(信用性/EDINET)をONで配線。

## 実施内容
- 新規 `scripts/run_morning_batch.py`（実行入口）：
  - `run_morning_batch(engine, dry_run, financials_fetcher=fetch_financials)` を実行＝**弾ON**
    （信用性S5＋EDINET開示S4b＋反証B群が magi_verify に効く）。
  - フラグ：`--no-quality`（弾OFF＝決定論のみ・低コスト/高速）／`--dry-run`（永続化抑制）。
  - 実行後、status・各ノード・summary・**当日の決裁待ち decision（碇の構えつき）**を表示。
  - 前提を明記：universe投入済・.env必須・LLM/ネット使用＝コスト注意（日次¥500枠）。決裁はmoomoo手動。
- テスト `tests/unit/test_morning_batch.py` +1
  （`run_morning_batch(..., financials_fetcher=stub)` で MELCHIOR反証に信用性が乗る＝弾ONの貫通）。

## 受入
- 全スイート green（362）。CLIは構文OK・touched ファイル ruff clean。
- ※フル live 実行はLLM/ネット課金が走るため自動実行はしない（入口を用意・実行はユーザー判断）。

## 使い方
```
.venv/bin/python scripts/load_universe.py            # 母集団投入（初回/更新時）
.venv/bin/python scripts/run_morning_batch.py        # 弾ONで pipeline 実行
.venv/bin/python scripts/run_morning_batch.py --no-quality  # 低コスト試走
```

## architecture.html
- §0：EDINET配線行のハッシュ確定＋「朝バッチCLI」行を追加。P5バッジを「発注リスト済/決裁手動」に。

## コミット
- 本md＋scripts/run_morning_batch.py＋test_morning_batch.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- これで **load_universe → run_morning_batch → （moomoo手動決裁）→ build_order_list** が実行可能な一連に。
- 残り：①監査意見/GC注記のXBRL深掘り ②定期実行（cron/スケジューラ）③P6評価（データ蓄積後）。
