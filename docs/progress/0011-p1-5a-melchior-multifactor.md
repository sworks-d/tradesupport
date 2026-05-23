# 0011 P1-5(a) MELCHIOR多面化 — 業績審判を2指標→7指標ルーブリックへ

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「計画に基づいて自走できるところまでは自走して。完了したらまとめて報告して。
  セクション区切りでコミットして。コミットに関しては全て許可する」
- （精度ドライバー②＝MELCHIOR深掘り。無料・判断不要のため自走で着手）

## 判断（スコープの切り分け）
spec P1-5 は本来 EDGAR/EDINET 一次情報(XBRL)まで含む大物（要EDINETキー）。
だが MELCHIOR が浅い直接原因は **9マップ中5を既定取得・判定は2指標(増収率・営業利益率)だけ** だったこと。
**無料・キー不要・判断不要**で効く部分＝(a)指標の多面化を先に実装し、一次情報(b)は残課題に分離。

## 実施内容
- `mcp_tools/fundamentals.py`
  - `_YF_FIELD_MAP` を拡張：成長(earnings_growth)・収益性(profit/gross margin・roa)・
    健全性(debt_to_equity・current/quick_ratio・free_cashflow・total_debt/cash)・
    バリュエーション(forward_per・price_to_sales・peg・beta)。
  - `_default_fields()` を MELCHIOR が見る **14指標** に拡張（取得不能はそのまま欠損＝na）。
- `magi/judges.py::melchior` を**多面ルーブリックに刷新**：
  - 成長×収益性×健全性×CF を pos/red に集約。**赤が1つでもあれば warn 寄り**（財務は保守的）。
  - buy＝growth_ok ∧ profit_ok ∧ 赤なし。確信度は使えた指標数・赤の数に連動。
  - reason は全指標を値つきで列挙（数値はコード値のみ＝R1）。欠損指標は評価から除外（R4）。
  - **既存3契約（強財務→buy/高・減収→warn・欠損→na）は維持**。
  - D/E は yfinance の%表記を比率へ正規化（>5 は /100）。

## 実測（受入）
- 既存3テスト＋新規 melchior 6・fundamentals 3 が green、全スイート green。
- ライブ NVDA：14指標取得。MELCHIOR reason が
  「増収率85%・営業利益率66%。」→
  **「増収率85%・純益成長214%・営業利益率66%・純利益率63%・ROE114%・D/E0.1・流動比率3.4。成長と収益性がともに良好。」** へ。
- 自分の触ったファイルは ruff clean。

## docs更新
- `spec/P1_collection.md` P1-5 を 🟡（(a)多面化✅／(b)一次情報❌の2段構成に整理）。
- `spec/P3_magi.md` P3-1 を ✅（多面化実績を追記）。

## コミット
- 本md＋fundamentals.py＋judges.py＋test_judges.py＋test_fundamentals.py＋spec(P1/P3)を同一コミット。

## 状態/次（既知の残・lint債務）
- 残：P1-5(b) 一次情報(EDGAR/EDINET・要キー)。MELCHIOR=Ollama解釈（P3-7の続き）。
- 自走の次：**P6 評価/バックテスト**（精度ドライバー③＝測って上げる）。
- 既存lint債務（本セッション未変更ファイル）：commander.py・demo_b1.py・test_defense.py・test_models.py に
  E501等が pre-existing。精度に無関係なので別途 chore で整理可。
