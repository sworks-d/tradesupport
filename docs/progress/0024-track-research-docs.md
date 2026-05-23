# 0024 研究docsをリポジトリに格納（設計の出所を追跡対象に）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「コミットして、アーキテクチャに反映して進めて」
  → 未追跡だった `docs/research/` の3doc をコミット、architecture.html に反映、その後 S5b+S6 へ進む。

## 実施内容
- `docs/research/` を追跡対象に：
  - `RESEARCH_METHODS.md`（手法・学術知見＝弾の知識ベース。領域1信用性/2反証/3スクリーニング/4評価＋第2波検証）
  - `RESEARCH_TO_IMPLEMENTATION.md`（「貫通→餌→弾」の接続ロードマップ。S1〜S7）
  - `RISK_EXOSKELETON.md`（規律層＝外骨格。元本¥100k・R-mult・集中/DD・8数値）
  これらは `DECISIONS.md`(D-23) と `spec/G_risk_discipline.md` が参照する**設計の出所**。
- `architecture.html` §0 のコミットログに本docコミット行を追加（設計の出所を明記）。

## 位置づけ
- 正本の階層：何を作るか＝`docs/plan/spec/`／どの順で＝`RESEARCH_TO_IMPLEMENTATION.md`（貫通→餌→弾）＋
  `IMPROVEMENT_PLAN_FOR_CODE.md`（A→B→C）／手法の知識＝`RESEARCH_METHODS.md`／規律＝`RISK_EXOSKELETON.md`。

## コミット
- 本md＋docs/research/3doc＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 次：**S5b＋S6 の配線**（弾を実際にMAGIへ効かせる）。
  ①S5b：Financials を magi_verify に通し `Verification.credibility_flag` を実値化（信用性warnが decision に乗る）。
  ②S6：M-Scoreの高変数（DSRI/TATA/AQI）を MELCHIOR の counter_within_domain にコード摘出（B-3のコード版）。
