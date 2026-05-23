# 進捗記録（docs/progress）

運用ルール（ユーザー指示・2026-05-23）：
- **コミットするたびに、新しい進捗md を1つ作成する**（連番 `NNNN-slug.md`）。
- 各mdには「**ユーザーが与えた指示**」「実施内容」「コミット」「状態/次」を記録する。
- 既存セッション分は `0001-session1-...md` にまとめてバックフィル。以降は1コミット=1md。

索引：
- `0001-session1-magi-rebuild.md` — セッション1（再構築計画〜B5・自走分まで）のまとめ
- `0002-progress-system-and-pipeline-flow.md` — 進捗記録システム導入＋pipeline.htmlをフロー図化
- `0003-pipeline-collection-detail.md` — データ収集をarchitecture粒度で詳細化（A-1〜A-5）
- `0004-detailed-implementation-plan.md` — 詳細実装計画(IMPLEMENTATION_PLAN.md)を作成
- `0005-spec-canonical-pipeline-plan.md` — パイプライン全網羅の正典実装計画(spec群)
- `0006-spec-depth-exemplar-p1-6.md` — specを実装レベルへ深掘り（見本P1-6）
- `0007-consolidate-plans-and-reuse-inventory.md` — 計画docs集約＋外部OSS棚卸し取り込み
- `0008-oss-borrow-policy.md` — 外部OSS借用方針(自前実装＋考え方補完)確定
- `0009-a2-news-self-build-casper-unblock.md` — A-2ニュース自前実装でCASPER解放（NVDA53件・CASPER=buy／D-22精度最優先）
- `0010-p3-7-casper-llm-interpretation.md` — P3-7 CASPER LLM解釈（=Sonnet）。数える→読むへ・決定論フォールバック付き
- `0011-p1-5a-melchior-multifactor.md` — P1-5(a) MELCHIOR多面化（2→14指標・成長×収益性×健全性×CFのルーブリック）
- `0012-a3-decisions-schema-magi-fit.md` — A-3 decisionsスキーマをMAGI適合（status/gendo_stance/verified_at追加・予測値nullable化）
- `0013-a1-universe-auto-define.md` — A-1 universe自動定義（US18+JP8の実在大型株を26件upsert・出所yfinance）
- `0014-a4-magi-dag-throughput.md` — A-4 MAGIをDAGに貫通（materialize→magi_verify・decision＋検証4表を永続化・冪等）
- `0015-b1-b2-counter-evidence-balthasar.md` — B-1/B-2 反証層土台（BALTHASARが自領域の逆向き事実をコード摘出・確証バイアス対策）
- `0016-b4-b5-commander-aggregation-internal-unease.md` — B-4/B-5 碇が反証を束ねる＋統合「全会一致でも内在不安」。architecture.html進捗運用を開始
- `0017-exoskeleton-params-capital-100k.md` — 規律層(外骨格)§1：リスク8数値の整合版確定＋元本¥100k（研究の貫通→餌→弾に転換）
- `0018-exoskeleton-universe-jp-first.md` — 規律層(外骨格)§2：universe を日本株主体に再編（JP14+US6・外れた銘柄は自動非活性）
