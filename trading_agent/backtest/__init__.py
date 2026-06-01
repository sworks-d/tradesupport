"""Phase 7 backtest フレームワーク（v2.10）。

数値ロジック（screening / financials / credibility）のみを過去再現で検証する。
LLM (MAGI/ZEELE/CASPER) の判定は対象外（コストとデータリーク防止のため）。

設計の核:
  - データソース: J-Quants Free Plan（過去 2 年分、5 req/分制限）
  - データリーク防止: 各時点で「その時点までに取得可能なデータのみ」を使う
  - ベースライン比較: 「現状ロジックの推奨 vs ランダム選択」のリターン分布

ハルシネーション対策:
  - 過去価格が取れない銘柄は除外（推測しない）
  - 分割・配当調整は J-Quants の AdjustmentClose を使う（自前計算しない）
  - 「サンプル数 < N」の場合は status="insufficient_data"
"""
