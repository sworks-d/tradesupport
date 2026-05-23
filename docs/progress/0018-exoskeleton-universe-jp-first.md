# 0018 規律層（外骨格）§2 — universe を日本株主体に再編

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- universe方針＝「日本株主体に再編（推奨）」（RISK_EXOSKELETON ⑧：¥100kでは為替スプレッドが効く）。

## 実施内容
- `scripts/load_universe.py`：
  - **JP主体・US従**に再編。JP14（セクター分散：自動車/電機/通信/銀行/IT/人材/素材/商社/ゲーム/医薬/部品/半導体装置/小売）
    ＋US6（メガキャップ）。ENTRIESもJPを先に。
  - **重要な前提反映**：moomoo は JP も**単元未満（ひと株）手数料0**＝単価が高くても1株から買える。
    よって¥100kでも高単価JP（例：ファストリ/任天堂）を1株単位で分散可能（サイジングは§3で単元未満対応）。
  - `upsert_universe` を**同期セマンティクス**に：渡した集合＝アクティブ母集団。
    リストから外した銘柄は `is_active=False`（旧26件の余剰がscreening母集団に残らない）。
- テスト `tests/unit/test_load_universe.py` +1（外した銘柄が非活性化される）。

## 実測（受入）
- ライブ再投入：fetched 20/20、**active total=20（JP14・US6）**。旧A-1で入れた余剰（AVGO/TSLA/…や旧JP）は非活性化。
- 全スイート green、touched ファイル ruff clean。

## 設計上の注記（サイジングへの申し送り）
- JP単元100株前提は **moomoo では不要**（単元未満で1株可）。現 `sizing.py` の `_JP_LOT=100` は §3 の
  R-multサイジングで「moomoo＝単元未満（1株granularity）」に見直す（高単価JPが¥100kで弾かれない）。

## architecture.html
- §0 コミットログに「外骨格§2」行を追加（§1のコミットハッシュも確定表記に）。

## コミット
- 本md＋load_universe.py＋test_load_universe.py＋architecture.html＋progress/README を同一コミット。
  （`data/trading.sqlite` は生成物＝gitignore。再投入は手動 or 朝バッチで）

## 状態/次
- §3：`recommend_position` を R-mult（stop逆算）＋20%上限＋現金下限＋（moomoo）JP単元未満 に拡張。
- §4：集中（テーマ/セクター≤2・≤30%）・現金≥20%・DD−15%新規停止。
- S3：A-5 決裁→発注リスト（規律を効かせた出口）。
