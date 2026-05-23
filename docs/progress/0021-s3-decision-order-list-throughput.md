# 0021 S3（A-5）決裁→発注リスト — 最初の貫通が通った

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「外骨格数値→S3出口」（規律層を実装し、S3＝決裁→発注リストの出口を作る）。

## 実施内容（A-5 / S3）
- 新規 `trading_agent/portfolio/orders.py`：
  - `decide(engine, decision_id, action, reason)`：decision を **approved/denied/held** に決裁
    （status＋user_action＋user_note＋user_acted_at 更新。既定の安全側は防御層 default_hold＝保留）。
  - `approved_buy_decisions(engine)`：承認済み買い decision を取得。
  - `build_order_list(tickers, price_lookup, sector_lookup, account_total, cash, held, peak)`（純粋関数）：
    承認銘柄に **R-multサイジング(§3)→ポートフォリオ規律ゲート(§4)** を適用し、
    `OrderLine(ticker, market, side, shares, amount_jpy, stop_price_jpy, risk_jpy(=想定損失≈1R), note)` を出力。
    reduce は最終額で株数再計算。US端株・JP1株単位。**自動発注しない（Tier1：人間がmoomooで手動）**。
- `portfolio/__init__.py` で公開。テスト `tests/unit/test_orders.py` 10
  （決裁：承認/保留/不正/不在/承認クエリ ＋ 発注：US&JP行/セクター3つ目block/DD全停止/価格無し除外/保有が上限に算入）。

## 実測（ライブ・最初の貫通）
- `universe（上位3：NVDA/GOOGL/AAPL）→ materialize → magi_verify → decide(approve) → build_order_list` を実走：
  ```
  発注リスト（元本¥100,000・現金下限20%）
    NVDA(US)  0.4862株 ¥16,667 stop¥30,167 想定損失¥2,000
    GOOGL(US) 0.2734株 ¥16,669 stop¥53,653 想定損失¥2,000
    AAPL(US)  0.2712株 ¥13,333 stop¥43,264 想定損失¥1,600  ← Tech重複でセクター30%枠により縮小
  ```
  **規律が live で効いた**：NVDA・AAPL は同一セクター(Technology)。AAPLはTech枠30%(¥30k)の残りに合わせ
  ¥16,667→¥13,333 へ自動縮小（想定損失も¥1,600に）。＝MAGIの外側で集中を抑える外骨格の実証。
- 全スイート green。touched ファイル ruff clean。

## 補足（運用メモ）
- `data/trading.sqlite` は A-3/B-1 のカラム追加前に作っていたため、**作り直して再投入**した
  （Phase1は create_all＝ALTER非対応。既存DBにカラムは増えない）。本番運用前にこの再作成手順を踏む。

## architecture.html
- §0 パイプラインを更新：P4✅(decision＋規律)・P5🟡(ロジック済/UI待)。コミットログに「S3」行、貫通完成を明記。

## コミット
- 本md＋portfolio/orders.py＋portfolio/__init__.py＋test_orders.py＋architecture.html＋progress/README を同一コミット。

## 状態/次（貫通の到達点）
- **最初の貫通が通った**：universe→…→碇→decision→決裁→規律を効かせた発注リスト。
- 残り：①UI決裁ボタン配線（FastAPI: api/routes_dashboard.py に decisions ルート＋フロント）
  ②S4 餌（2期分財務＋EDINET）→ S5-7 弾（M/F/Z信用性・コード反証・V字）③P6評価（R-mult記録）。
