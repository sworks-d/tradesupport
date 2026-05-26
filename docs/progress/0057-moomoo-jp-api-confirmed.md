# 0057 — moomoo JP OpenAPI 実機確定（同意②クリア）＋ MoomooBroker 通貨修正

## ユーザーが与えた指示
- 「APIはどうなん？ moomooで進めようと思ってる」→OpenD起動→同意②対応。

## 実施内容（実機診断で確定）
- `scripts/probe_moomoo.py`（OpenD 127.0.0.1:11111・FUTUJP）で疎通確認：
  - **「日本リージョン非対応」は誤り**。唯一の壁は**同意②(免責 disclaimer)**。同意＋OpenD再起動で **JP/REAL の保有読取 OK**。
  - **JP は SIMULATE 非対応**（API紙なし）→ 紙は自前 paper_exec を継続。US は SIMULATE/REAL 両OK。
  - **accinfo_query は currency 指定必須**：JP口座は `currency=JPY`（未指定で変換エラー）。
- 実バグ修正 `trading_agent/brokers/moomoo.py`：`MoomooBroker` に `currency`（既定JPY）を追加し `accinfo_query(currency=…)` に渡す。
  - **ライブ実証**：`MoomooBroker(trd_env="REAL", markets=("JP",), currency="JPY")` → `Account(cash=0,total_assets=0,currency=JPY)` / `positions=[]`（口座開通済・**入金前**）。

## 検証
- ruff/mypy clean・test_brokers green・tests/unit 全 green。ライブで JP/REAL 読取を実証。

## 状態 / 次
- moomoo JP API は**接続・読取が確定**。だが口座は空（入金前）＝**ペーパーは StandIn ¥100k 継続**、moomoo REAL同期は入金後に有効化。
- MoomooBroker は読取のみ（発注メソッド無し）＝REAL接続でも自動発注は構造上不可（Tier1手動）。
- 次：入金後に build_snapshot/run_paper の moomoo REAL 同期を有効化（trd_env=REAL・markets=JP）。それまでは紙運用。
- メモ更新：`moomoo-jp-api-status`（MEMORY索引にも追加）。
