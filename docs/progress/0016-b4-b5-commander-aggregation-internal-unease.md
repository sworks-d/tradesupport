# 0016 B-4/B-5 反証の統合 — 碇が束ね、統合機構が「内在不安」を出す

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で再開して」（B群）。
- 「更新のたびに、architecture.htmlを更新して、進捗も入れて視覚的に確認できるようにして。これはコミットとセット。」
  → **本コミットから architecture.html に §0 実装進捗（パイプライン状態＋コミットログ）を追加し、毎コミットで更新する運用に。**

## 実施内容（B-4 / B-5）
- `magi/commander.py`（B-4）：`_aggregate_counters` を新設し、各審判の `counter_within_domain` を
  「各審判の内在反証＝役割：claim／…」として反対論拠に追記。碇の独自生成ではなく**審判の摘出を束ねるだけ**＝
  MAGI外の新事実なし（magi_compliant維持・R5）。src_note も「3審判の判定と各審判の内在反証のみ」に更新。
  併せて当ファイルの既存E501（pre-existing）3行を整形。
- `magi/integration.py`（B-5）：全会一致(buy)でも**反証を持つ審判が≥2**なら interpretation を
  「内在不安・全会一致でも確信度は割り引くべき」に。集計のみ・推奨しない・総合スコアなし。
- テスト：`test_integration_commander.py` +4（碇の反証集約・反証なし時は無付与／内在不安発火・単独反証は従来通り）。

## architecture.html（コミットとセットの新運用）
- §0「実装進捗」を新設：パイプライン6フェーズの状態バッジ（P1✅…P3✅/P4🟡/P5❌/P6❌）＋
  本セッションのコミットログ表（A-2〜B-4/B-5）。CSS（.pipeline/.pstage/凡例）を追加。subtitle 日付更新。
- 以後、**1コミット＝1セクション**ごとに当表へ1行追加・バッジ更新する。

## 実測（受入）
- 全スイート green（291）。touched ファイル ruff clean（commander.py の pre-existing E501 も解消）。
- ライブ AAPL：split=「業績◯・株価✕」、碇=「反対するなら：BALTHASARが慎重…／各審判の内在反証＝株価：MACDヒストグラムが強気」。

## docs更新
- `spec/P3_magi.md` P3-11/P3-12 を ✅。`IMPROVEMENT_PLAN_FOR_CODE.md` B-4/B-5 を ✅（実績追記）。

## コミット
- 本md＋commander.py＋integration.py＋test_integration_commander.py＋architecture.html＋spec/P3＋IMPROVEMENT_PLAN＋README を同一コミット。

## 状態/次
- B群の決定論部（B-1/B-2/B-4/B-5）が完了。残るは **B-3（MELCHIOR/CASPER の LLM反証＋防御層での実在照合）**＝
  「逆向きの事実をデータから選べ・創作禁止」→ source_refs 必須・照合不通過は捨てる（繊細・LLM）。
- 申し送り：docs/research のmd（前回空だった）が保存され次第、設計に反映。
