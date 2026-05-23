# 0007 計画docsの集約＋外部OSS棚卸しの取り込み

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- （IMPROVEMENT_PLAN_FOR_CODE.md / X1_reuse_inventory.md を提示し）「これを確認して」
- 「進めて、で、外部OSSについてはどう思う？」

## 実施内容
- 2docを実コードで検証＝`§0 現状の事実`は全て事実と一致（morning_batchにMAGI無し／newsにyf/GNews無し／
  decisionsがver1形／MAGI4表は定義済／universe空）。補足：MAGIは build_snapshot 経由では実行済（断線はDAG/DB側）。
- **散在防止のため docs/ 直下から docs/plan/ へ集約**：
  - `docs/IMPROVEMENT_PLAN_FOR_CODE.md` → `docs/plan/`（実行ロードマップ）。X1への参照を `spec/X1_...` に修正。
  - `docs/X1_reuse_inventory.md` → `docs/plan/spec/`（X-1成果物）。X_external_reuse 参照を同ディレクトリに修正。
- `docs/plan/README.md` 構成表を更新（設計の正典=spec/、実行順=IMPROVEMENT_PLAN、X1=spec配下）。
- spec/P3 に **反証層（B群 P3-8〜P3-12）** を取り込み（詳細はIMPROVEMENT_PLAN B群を正）。

## 正典の階層（確定）
- 「何を作るか」＝`docs/plan/spec/`（00_overview＋P1..P6/X/U）。
- 「どの順で実装するか」＝`docs/plan/IMPROVEMENT_PLAN_FOR_CODE.md`（A断線解消→B反証→C OSS借用）。
- 外部OSS借用＝`docs/plan/spec/X1_reuse_inventory.md`。

## コミット
- 本md＋移動2ファイル＋README/P3更新を同一コミット。

## 状態/次（要ユーザー決定）
- 原則7のrefinement（自己改変NG/参照提示OK）採用可否／A-3 schema確定の確認／A-1銘柄リスト出所／C-0ライセンス。
- 実装の最短着手：A-2 news（無料・CASPER起動）。
