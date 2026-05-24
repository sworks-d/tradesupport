# 0044 — 即効：利確キャップ撤廃（B'出口）＋目標値の是正

## ユーザーが与えた指示
- 「ペーパー運用まで100%。全タスク化して朝までに自走完了」＋優先順位リスト。
- 即効①利確キャップ撤廃（sell_recommender の利確ロジックを撤廃 or トレーリング化）。理由：自分のBTで「利確が収益の2/3を破壊」と実証済み。勝ち玉を刈る＝複利を殺す。最優先。
- 即効②目標値の是正：「+30〜50%/年」を破棄→「リスク調整後でパッシブ超え＋生存」へ。無根拠の目標がレバ・集中・利確回転＝規律破壊を誘発。

## 実施内容
- **B'出口（利確キャップ撤廃）** `trading_agent/agents/sell_recommender.py`：
  - 利確でサイズを刻むロジックを**全廃**（旧 `profit_taking_score`／score≥50で半量／score≥90で全量／目標利確を削除）。
  - サイズを減らす出口は2つだけ＝**①固定stop到達（pnl≤stop_loss_pct）＝全量・成行・常に規律メッセージ／②保有期限 target_date 到達＝time_exit で全量手仕舞い**。それ以外は勝ち放任（シグナル無し）。
  - 「下方向は機械優先・上方向は放任」の非対称設計。`determine_sell_recommendation(signal_type, qty, current_price)`（score引数を除去）。
  - `models/signals.py`：SellSignal.signal_type コメントを `stop_loss`/`time_exit` に更新（下流は表示のみ＝routes_dashboard で echo、分岐なしを確認）。
- **目標値の是正** `docs/plan/spec/00_overview.md §1`：年+30〜50% を破棄し「リスク調整後（Sharpe/最大DD）でパッシブ超え＋生存」へ。稼ぎ＝所有×時間×複利（β）／「地雷だけ売らせて持ち続けさせる機」を明記。

## 検証
- 既存ハーネス `backtest_signal` で time-exit を**コードを書く前に裏取り**：同一GCエントリーで出口だけ変えると capped(+20%利確)=逐次複利+132% → time-exit(保有延長)=最大+406%（利確キャップが収益の約2/3を破壊を production 経路で再現）。
- `tests/unit/test_sell_recommender.py` を新挙動に書換（勝ち放任=売りシグナル無し／固定stop=全量規律／time_exit=全量）。
- 回帰：`test_morning_batch.py` のフィクスチャを「保有期限経過→time_exit発火」に修正。
- ruff All passed・mypy(sell_recommender) clean・tests/ 全 green。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：即効①②完了。トレーリング(peak状態)は別タスク（任意の上積み）として保留。
- 次：心臓（偽確信度除去・スクリーニング検品純化）→構造（逓減・Core-Satellite）→ペーパー執行ループ。
