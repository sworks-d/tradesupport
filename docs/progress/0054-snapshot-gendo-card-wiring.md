# 0054 — build_snapshot を GENDO推奨カードに繋ぎ替え（UI反映#1：データ）

## ユーザーが与えた指示
- 「これってUIに反映されてる？」→ ほぼ未反映と判明。「推奨で実装して」＝#1 snapshot繋ぎ替え＋碇stance修正。

## 実施内容
- `scripts/build_snapshot.py::_serialize_candidate`：
  - 各候補に **`gendo_card`** を追加（action/sleeve/reason/counter/guardrail/defense_confidence/offense_confidence/learn_note）。`gendo_recommend()` を split/vr/cmd＋SizeRec から呼ぶ（守り主導・攻めは灰色・新事実なし）。
  - **碇stanceの綻び修正**：旧 `buys==len(verdicts)`（BALTHASAR込み）→ `derive_gendo_stance()`（投票=業績・文脈のみ）に。非投票の決定と画面を整合。
  - `_build_candidates` から SizeRec(`rec`) を渡す。

## 検証
- `build_snapshot.py --demo`（オフライン）で snapshot.json に gendo_card 8項目を確認。ruff All passed。
- ※mypy の2件(354/366 holdings部 union-attr/reconciliation)は本変更外の既存warn。

## コミット / 状態 / 次
- コミット：本md と同コミット。
- 状態：UI反映#1完了＝**カードのデータは snapshot.json に乗った**。
- 次：#2 フロント（ui/ のダッシュボードに GENDOカードを描画）。
