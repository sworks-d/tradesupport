# 0004 詳細実装計画の作成（指摘対応）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「docsのplanの中に詳細な実装計画を作成してって言ったけど、作ってなくない？」（B0_DIFF_PLAN.mdを開いた状態で指摘）

## 認識（正直な反省）
- docs/plan には README(戦略)/TASKS(進捗)/DECISIONS(判断)/B0_DIFF_PLAN(B0のみ) はあったが、
  **全工程を粒度細かく落とした「詳細な実装計画」が無かった**。TASKSはExit条件止まりで、
  データ収集・ニュース・一次情報・スクリーニング・外部リポジトリ借用・決裁→発注→評価の
  具体的な作り方が書かれていなかった。

## 実施内容
- `docs/plan/IMPLEMENTATION_PLAN.md` を新規作成。**WP（作業パッケージ）単位**で
  「目的／対象ファイル／データソース／実装内容／受入条件／依存／規模」を記載：
  - Phase1：A1ニュース収集(無料・銘柄別)／A2 財務一次情報＋信用性(D-14)／A3 相場本番化
  - Phase2：B1 universe投入／B2 スクリーニング起動／B3 候補→MAGI接続
  - Phase3：X1 外部リポジトリ棚卸し／X2 借用部品移植
  - Phase4：C1 decisions schema再構成／C2 B6永続化／C3 決裁UI／C4 発注／C5 評価
  - Phase5：U1 コンポーネント化＋視覚回帰／U2 新規6画面／U3 LLM接続
  - 推奨順序（依存図）・受入/テスト方針も記載。
- `README.md` 構成表に IMPLEMENTATION_PLAN / B0_DIFF_PLAN / pipeline.html を追記。

## コミット
- 本md＋IMPLEMENTATION_PLAN.md＋README更新を同一コミット。

## 状態/次
- 詳細計画をユーザーレビュー待ち。最短着手は WP-A1（ニュース収集・無料）。
