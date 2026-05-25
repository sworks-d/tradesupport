# 0046 — 構造：逓減スケジュール（C2）＋ Core-Satellite 比率（C1 params）

## ユーザーが与えた指示
- 構造「逓減スケジュール追加」：口座サイズ→risk%→銘柄数の表を G-0 と params.py へ。理由：今は5銘柄固定で口座が育っても分散が広がらない＝歪み分布の哲学と矛盾。
- 構造「Core-Satellite構造を明示」：コア(8〜9割)＝質分散の積立・放任／サテライト(1〜2割)＝小さく隔離した賭け。理由：稼ぎ＝所有×時間×複利。V字/小型の本能を死なない範囲に隔離。

## 実施内容
- `trading_agent/risk/params.py`：
  - **逓減スケジュール**：`TaperTier` ＋ `TAPER_SCHEDULE`（口座<30万:2.0%/5、〜100万:1.5%/8、〜300万:1.2%/12、〜1000万:1.0%/16、≥1000万:0.8%/20、現金下限も逓減）＋ `params_for_account(total)`（口座サイズで risk%↓・銘柄数↑、stop幅/上限/ゲートは base 継承）。思想＝偏った分布(Bessembinder)で大化けを取りこぼさない網を広げる。
  - **Core-Satellite**：`core_fraction=0.85`/`satellite_fraction=0.15` ＋ `core_budget_jpy()/satellite_budget_jpy()`。
- `docs/plan/spec/G_risk_discipline.md`：G-7（逓減表）・G-8（Core-Satellite）を追記。

## 検証
- `tests/unit/test_risk_params.py` に C1/C2 テスト追加（比率和=1・コア≥0.8／逓減の単調性：risk%単調減・銘柄数単調増／base継承）。
- ruff All passed・mypy(params) clean・tests/ 全 green。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：構造の**パラメータ層**は完了。**配分の実配線**（コア=検品生存の分散塊／サテライト=スクリーニング予測信号）は B1（検品純化）の確定後＝MORNING_REVIEW で朝に確認。
- 次：B1 検品純化の確定、ペーパー執行ループ（MORNING_REVIEW に設計集約）。
