# 0019 規律層（外骨格）§3 — R-multサイジング（リスクベース）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「外骨格数値→S3出口」（§1で数値確定済→規律をサイジングに実装し、最終的にS3出口へ）。

## 実施内容（G-1/G-2/G-4）
- `portfolio/sizing.py::recommend_position` を「20%上限のみ」から**R-mult（リスクベース）**へ刷新：
  - **1R = 口座 × 2%**（`RiskParams.one_r_jpy`）。**anti-martingale**＝残高が減れば1Rも縮む（連敗ブレーキ）。
  - **R-mult額 = 1R ÷ 損切り%**（損切りが広い銘柄は小さく・狭い銘柄は大きく＝全銘柄のリスクを揃える）。
  - **budget = min(R-mult額, 総資産×20%上限, 使える現金=現金−総資産×現金下限20%)**。
  - **US＝端株（小数株）／JP＝moomoo単元未満（1株単位）**（§2の前提反映。`_JP_LOT=100` 廃止）。
  - 透明性：`SizeRec` に `stop_pct / stop_price_jpy / risk_jpy(=想定損失≈1R) / binding(r-mult|cap|cash)` を追加。
- 規律が効く具体例（テストで固定）：
  - stop10%→¥20,000（=20%上限ちょうど）。stop20%→¥10,000（広いstopは小さく）。stop5%→上限¥20,000で抑制（保守化）。
  - 高単価JP（¥40,000/株）は1株でも1R超→**購入不可**（規律が弾く）。現金下限で買えない時も算定不可。
- テスト `tests/unit/test_sizing.py` 全面刷新（9件：R-mult/広stop/上限binding/現金制約/現金下限ブロック/JP1株/高単価却下/
  anti-martingale縮小/既定stop）。

## 実測（受入）
- 全スイート green。`portfolio/` と test は ruff clean。
- ライブ snapshot（¥100k）：NVDA＝¥16,666（1R¥2,000÷stop12%・binding=r-mult）・端株0.4863・weight16.7%。
  元本¥100kとR-mult規律が UI に反映。

## architecture.html
- §0 コミットログに「外骨格§3」行を追加（§2のハッシュ確定表記）。

## コミット
- 本md＋portfolio/sizing.py＋test_sizing.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- §4：ポートフォリオ規律ノード（同時保有≤5・1テーマ/セクター≤2かつ≤30%・現金≥20%・高値比−15%で新規停止）。
  MAGIが各々「買い」でも、Gが集中/DDで**サイズ抑制/可否warn**を掛ける（MAGIの外側）。
- S3：A-5 決裁→発注リスト。発注リストに R-mult サイズ＋stop価格＋想定損失(1R) を併記（G-4）。
