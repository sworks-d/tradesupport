# P4 提案生成・サイジング — フェーズ設計＋タスク設計

**フェーズの役割**：MAGI結果を1銘柄1件の `decision`（提案）にまとめ、予算内のポジションサイズを付けてDBに保存。
**全体ゴール寄与**：人間が決裁できる「提案」を、検証結果・サイジング込みで生成する。
**前からの引き継ぎ**：P3（judge_verdict/split/verification/commander）＋P1-10口座。**次への引き渡し**：
`decision`（status/gendo_stance/サイジング）＋紐づくMAGI 4表（→P5決裁、U表示、P6評価）。
**フェーズのハルシネ防止方針**：R1サイジングはコード計算／R6 default_hold を decision.status の既定に反映／R7 P3出力のみ使用。

> 状態：✅ / 🟡 / ❌ / 🔵要判断

### P4-1 ポジションサイジング  〔✅〕
- 全体ゴール：「¥1Mで何をいくら買うか」を分散原則内で算定。
- 前からの引き継ぎ：候補の実価格(P1-1)＋口座cash/total(P1-10)＋USD/JPY。
- 目的：1銘柄20%上限・現金内・米株端株/日本株単元で推奨額・株数。
- 実装：`portfolio/sizing.py::recommend_position`。
- 次への引き渡し：`SizeRec(amount_jpy, shares, weight, note)`（→decision, U候補カード）。
- ハルシネ防止：R1 すべてコード計算（LLM不使用）／R4 価格無しは算定不可。
- 受入：US端株・JP単元・予算超過の不可判定（既存テスト green）。

### P4-2 decisions スキーマ再構成  〔🔵 要判断〕
- 全体ゴール：ver1形（総合スコア前提）の `decisions` をMAGIに適合させ、提案を保存可能に。
- 前からの引き継ぎ：現行 `models/decisions.py`（score/expected_return/scenarios/thesis/evaluation_date が必須）。
- 目的：`status`(verifying→…→holding)・`gendo_stance` 追加。ver1必須項目を nullable化 or 別表分離。
- 実装：`models/decisions.py`＋マイグレーション＋`test_models`。**🔵schema方針はユーザー確認**（score残すが非表示=D-06）。
- 次への引き渡し：MAGI候補を保存できる decision スキーマ（→P4-3）。
- ハルシネ防止：（スキーマ）— ／ R7 既存16表は無改変、追加のみ。
- 受入：MAGI候補から decision を作成・保存できる。既存テスト維持。

### P4-3 decision生成（materialize_decisions）  〔✅ A-4〕
- 全体ゴール：候補→決裁待ちの提案レコードを生成。
- 前からの引き継ぎ：P2/P3を通った候補＋P3結果。
- 目的：active候補から `decision` 行を作成（status=verifying）。gendo_stance=碇/割れから導出。
- 実装：新規 `magi/persist.py::materialize_decisions`（or morning_batchノード）。
- 次への引き渡し：decision行（→P4-4で検証結果を付与）。
- ハルシネ防止：R7 候補に無い銘柄を作らない／R2 decisionに時点を持たせる。
- 受入：候補N→decision N行（status=verifying）。

### P4-4 MAGI永続化（magi_verify）  〔✅ A-4・検証4表をdecision_id付きで永続化〕
- 全体ゴール：decisionに3審判→防御→統合→碇の結果をDB保存し、UIに出す前に検証を完了させる。
- 前からの引き継ぎ：decision行（P4-3）＋P3各結果。
- 目的：`judge_verdict×3 / split_pattern / verification / commander_rec` を decision_id付きで保存。
  検証完了で status: verifying→verified→awaiting。割れ/未照合→既定保留。
- 実装：`magi/persist.py`（or morning_batch に materialize→magi_verify ノード挿入、B0_DIFF_PLAN §2）。
- 次への引き渡し：検証済み decision＋MAGI 4表（→P5決裁、U詳細）。
- ハルシネ防止：R6 default_hold を status/既定決裁に反映／R2 全行に出典/時点／R5 碇MAGI準拠を保存。
- 受入：1銘柄がDBに decision＋MAGI 4表付きで保存され status 遷移（テスト）。
