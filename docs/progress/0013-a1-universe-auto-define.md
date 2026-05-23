# 0013 A-1 universe投入 — 母集団を自動定義（NVDA決め打ち脱却）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- （A-4の前提＝銘柄ユニバースの出所について）「自動定義（推奨）」を選択。
  ＝¥100万・端株前提でUS主要＋安価な日本株から、出所の明確な実在銘柄のみで母集団を自動生成。
- 「セクション区切りでコミット／コミットは全て許可」「自走できるところまで」。

## 実施内容
- 新規 `scripts/load_universe.py`：
  - **キュレーション（実在・検証可能・発明しない）**：US大型18（AAPL/MSFT/NVDA/GOOGL/AMZN/META… 端株可で単価不問）
    ＋JP高流動・比較的安価8（9432 NTT/7267 ホンダ/8306 三菱UFJ/6178 日本郵政/3382 セブン&アイ/9434 SB/6501 日立/7203 トヨタ）。
  - メタ（社名/セクター/時価総額/出来高）は **yfinance から取得**（出所明確＝R7）。取得不能は静かにスキップ。
  - `build_rows`（US時価総額はUSDJPY換算・JPは据え置き）＋`upsert_universe`（ticker主キーで冪等）。
  - `--dry-run` 対応。`screening_agent._load_universe` が market_cap_jpy 降順で読む形に整合。
- `pyproject.toml`：`[tool.pytest.ini_options] pythonpath=["."]` を追加し、scripts/ のロジックをテストから import 可能に。
- テスト `tests/unit/test_load_universe.py` 7件：キュレーション健全性（US=英字/JP=数字・market付与）・
  JPY換算（US×usdjpy / JP据え置き）・取得不能スキップ・冪等upsert（重複しない）・降順読取。

## 実測（受入）
- ライブ実行：**fetched 26/26（usdjpy=159.2）→ `data/trading.sqlite` に 26件 upsert**（tables=21）。
  上位 NVDA/GOOGL/AAPL/MSFT/AMZN。`data/*.sqlite` は gitignore 済（コミットしない）。
- 全スイート green、触ったファイルは ruff clean。

## docs更新
- `IMPROVEMENT_PLAN_FOR_CODE.md` A-1 を ✅（自動定義採用・実績追記）。

## コミット
- 本md＋scripts/load_universe.py＋test_load_universe.py＋pyproject.toml＋IMPROVEMENT_PLAN＋README を同一コミット。
  （`data/trading.sqlite` は生成物のためコミットしない＝gitignore）

## 状態/次
- **母集団が入った＝screening が回せる＝A-4 の materialize_decisions が候補を持てる**。
- 次の自走：**A-4（MAGIをDAGに貫通）**＝候補→Decision(verifying)生成→3審判→防御→統合→碇→
  judge_verdict×3/split/verification/commander_rec を decision_id 付きで保存→status verifying→verified→awaiting。
