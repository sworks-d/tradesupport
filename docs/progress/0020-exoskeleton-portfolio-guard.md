# 0020 規律層（外骨格）§4 — ポートフォリオ規律ゲート（集中・DD）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「外骨格数値→S3出口」（規律層を実装し、S3出口へ）。本§はその規律ゲート（G-3）。

## 実施内容（G-3）
- 新規 `trading_agent/risk/portfolio_guard.py`：`evaluate_portfolio_guard(candidates, held, account_total, peak_total)`。
  MAGIが各候補を独立に「買い」と出しても、**ポートフォリオ全体で制約**する（判断でなく規律）：
  - **DD新規停止**：高値から−15%（`drawdown_halt`）到達で全新規 block＋`halt_new=True`。
  - **同時保有上限**：保有＋採用が5（`max_positions`）を超えたら block（満員）。
  - **セクター集中**：同一セクター2銘柄（`max_per_theme`）超で block＝**AI3銘柄＝実質1ベットを止める**。
  - **セクター金額**：同一セクターが口座30%（`max_theme_weight`）超は **reduce**（枠に合わせ縮小）。
  - サイジング0（R-mult/予算で買えない）も block。候補は順に貪欲評価（上位＝MAGI確信度順を想定）。
  - 出力 `GuardVerdict(action: allow/reduce/block, amount_jpy, reason)` ＋ `allowed()`。
- `risk/__init__.py` で公開（Candidate/Held/GuardVerdict/PortfolioGuardResult/evaluate_portfolio_guard）。
- テスト `tests/unit/test_portfolio_guard.py` 8（DD停止/帯内OK/満員block/同一セクター3つ目block/30%超reduce/
  別セクターは両許可/サイジング0 block）。

## 受入
- 全スイート green。`risk/` と test は ruff clean。全て決定論（数値はコード・LLM非関与）。

## 設計上の位置づけ
`universe→screening→MAGI→decision→【G: サイジング(§3)＋規律ゲート(§4)】→決裁→発注`。
MAGIは「何を/どの向きで」、外骨格は「いくつ・どれだけ・止めるか」。S3でこのゲートを通した発注リストを出す。

## architecture.html
- §0 コミットログに「外骨格§4」行を追加（§3ハッシュ確定表記）。

## コミット
- 本md＋risk/portfolio_guard.py＋risk/__init__.py＋test_portfolio_guard.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 外骨格の主要部品（§1数値・§2universe・§3サイジング・§4規律）が揃った。
- 次＝**S3（A-5 決裁→発注リスト）**：awaiting decision に対し
  ①決裁API（approved/denied/held＋status更新・既定は防御層default_holdに従い「保留」）
  ②サイジング(§3)＋規律ゲート(§4)を通した**発注リスト出力**（銘柄/数量/stop価格/想定損失1R）。
  ＝「最初の貫通」完成（universe→…→碇→decision→決裁→発注リスト）。FastAPI層あり（api/routes_dashboard.py）。
