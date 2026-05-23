# 0022 S4a（餌）— 2期分財務の取得（弾の物理的前提）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「決済はmoomooの画面で行うから後でいい。それ以外で実装すべきことから進めて。」
  → UI決裁/発注の配線は後回し（moomooで手動）。研究の順序「貫通→餌→弾」で、貫通の次＝**餌(S4)**へ。

## 判断
研究の共通律速＝「M-Score/F-Score/Z-Score・MELCHIORコード反証はすべて**2期分財務**が必須」。
EDINET/EDGARの一次情報（監査意見/GC注記＝S4b）は**要キー**だが、**2期分の主要ライン項目は
yfinanceの財務諸表（income_stmt/balance_sheet/cashflow）からキー不要で取れる**。
→ まずキー不要の **S4a（2期財務）** を実装し、S5（M/F/Z）・S6（MELCHIOR反証）を解禁する。

## 実施内容（S4a）
- 新規 `trading_agent/screening/financials.py`：
  - `fetch_financials(ticker, fetcher=…, market_cap=…) -> Financials`（current/prior の2期）。
  - `PeriodFinancials`：M/F/Z・反証に要る18ライン（revenue/cogs/gross_profit/net_income/sga/depreciation/
    ebit/total_assets/current_assets/current_liabilities/ppe/receivables/total_liabilities/long_term_debt/
    retained_earnings/shares/working_capital/operating_cashflow）。
  - yfinance ラベルは候補リストでフォールバック（例 revenue＝Total Revenue→Operating Revenue）。
  - working_capital 欠損は 流動資産−流動負債 で補完（コード計算）。**欠損は None（R4・捏造しない）／数値はコード（R1）**。
  - `_fetch_yfinance_statements` が3表を {periods, rows} にマージ（NaN→None・期はTimestamp→ISO）。fetcher注入でテスト可能。
- `screening/__init__.py` で公開。テスト `tests/unit/test_financials.py` 6（2期マップ/WC補完/ラベルフォールバック/
  欠損None/NaN扱い/空はNone）。

## 実測（受入）
- ライブ NVDA：2期取得（当期2026-01-31・前期2025-01-31）。revenue 215.9B/130.5B・receivables 38.5B/23.1B・
  op_cf 102.7B・net_income 120.1B。＝前期比が要る指標（DSRI等）が計算可能に。
- 全スイート green、touched ファイル ruff clean。

## architecture.html
- §0 コミットログに「S4a（餌）」行を追加（S3ハッシュ確定表記）。

## コミット
- 本md＋screening/(financials.py,__init__)＋test_financials.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- **餌が入った**＝S5（信用性 Beneish M-Score / Piotroski F-Score / Altman Z-Score＝複数独立warnフラグ）と
  S6（MELCHIORコード反証＝DSRI/TATA/AQI等の利益の質）が解禁。次はS5から。
- 残り S4b：EDINET/EDGAR の一次情報（監査意見/GC注記）＝**要EDINETキー**（ユーザー作業）。M/F/Zはキー無しで先行可。
