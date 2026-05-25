# 0055 — ダッシュボードに GENDO推奨カードを描画（UI反映#2：フロント）

## ユーザーが与えた指示
- 「これってUIに反映されてる？」→未反映。「推奨で実装して」＝#2 フロント描画。

## 実施内容
- `ui/app/LiveData.tsx`：
  - `Candidate` 型に `gendo_card?`（action/sleeve/reason/counter/guardrail/defense_confidence/offense_confidence/learn_note）を追加。
  - 候補詳細パネルの碇ゾーンを**初心者向けGENDO推奨カードに格上げ**：`.cmd-rec`＝「GENDO推奨：{action}（sleeve） {reason}」、`.cmd-src`＝「従うなら：{guardrail} ｜ 確信度 守り●/攻め○ ｜ {学べる一言}」。`.cmd-counter` は碇の反対論拠を維持。
  - 静的テンプレート(dashboard.html)は非改変＝既存DOMスロットに流し込む低リスク方式。

## 検証
- `npx tsc --noEmit` クリーン（型エラー0）／`eslint` クリーン／python tests/unit 全green（回帰なし）。
- snapshot.json は未追跡の生成物（--demo で gendo_card 入りを確認済・0054）。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：**UI反映完了**＝build_snapshot(#0054 データ)→snapshot.json→ダッシュボード碇ゾーンに GENDO推奨カードが表示される。ターミナル限定だったコーチカードが画面に出た。
- 次（残）：P4-4 評価の向け直し／攻め昇格条件（前向き較正＋コントロール参戦）／P4-2 sleeve永続化／専用カードUI（見た目の作り込みは別途・現状は既存ゾーンに格上げ）。
