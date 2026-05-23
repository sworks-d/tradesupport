# 0012 A-3 decisions スキーマ再構成 — MAGI候補を保存できる形へ

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「計画に基づいて自走できるところまでは自走して。完了したらまとめて報告して。
  セクション区切りでコミットして。コミットに関しては全て許可する」

## 判断（なぜA-3を自走対象に）
精度ドライバー③(P6評価)・④(B群反証)は**いずれもA-4(DAG接続＝判断の永続化)が前提**。
そのA-4の前提が **A-3 decisionsスキーマ**。A-3はIMPROVEMENT_PLANで「★最初にやる／方針確定済（ユーザー確定・
叩き台ではない）／依存なし」と明記され、**Decision生成箇所が皆無＝後方互換リスク最小**。
よって「計画に基づく自走」の正しい次手はA-3。これを通せば残り精度ドライバーの土台が開く。

## 実施内容
- `models/decisions.py`（ver1形→MAGI適合。**既存カラムは1つも削除せず**）：
  - **追加**：`status`（既定 "verifying"・index／ライフサイクル）、`gendo_stance`（碇の構え・nullable）、
    `verified_at`（MAGI検証完了時点・nullable）。
  - **nullable化**：`score`（残すがUIに総合点として出さない＝D-06／A-B育成用）、`expected_return`、
    `target_period_days`、`thesis_at_decision`、`evaluation_date`（候補生成時=verifyingでは未確定のため）。
  - `DECISION_STATUSES`（9遷移：verifying→verified→awaiting→approved/denied/held→order_listed→ordered→holding）を定義。
- `models/__init__.py`：`DECISION_STATUSES` を公開。
- スキーマは `create_all` 生成（Phase1はAlembic未運用・versions空）のため**マイグレーション不要**。
- テスト `tests/unit/test_models.py` +2：
  - MAGI候補の最小生成（date/ticker/actionのみ→status=verifying・予測値は全てNone・hit_or_miss=pending）。
  - ライフサイクル遷移（verifying→verified・gendo_stance/verified_at を充填、status∈DECISION_STATUSES）。

## 受入
- 全スイート green（テーブル数は21のまま＝新表なし・列追加のみ）。
- **Decision生成箇所が無いため既存テストは無改変**。自分の触ったファイルは ruff clean。

## docs更新
- `IMPROVEMENT_PLAN_FOR_CODE.md` A-3 を ✅（実績追記）。

## コミット
- 本md＋decisions.py＋models/__init__.py＋test_models.py＋IMPROVEMENT_PLAN＋README を同一コミット。

## 状態/次（自走の到達点と判断ゲート）
- **A-3完了でA-4(DAG接続)の保存先が用意できた**。だがA-4の実用には以下のユーザー判断が必要：
  - **A-1 universe（銘柄リストの出所）**＝[要ユーザー判断]。母集団が無いとscreening候補が出ず、
    DAGのmaterialize_decisionsが回す対象が無い（現状CANDIDATESはNVDA決め打ち）。
  - A-4のDAG挿入方針（既存market_analystを残しつつMAGIを正にする）の最終GO。
- ここまでで精度3本（A-2/P3-7/P1-5a）＋土台1本（A-3）が完了。次の精度ドライバー(③④)はA-4後。
