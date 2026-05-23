# 0025 S5b/S6 — 信用性フィルタをMAGIに配線（弾を効かせる）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「コミットして、アーキテクチャに反映して進めて」（研究docsをコミット後、推奨の S5b+S6 へ）。

## 実施内容（S5b 配線 ＋ S6 MELCHIORコード反証）
- `magi/defense.py::verify`：`credibility_flag: str="ok"` 引数を追加。**warn（粉飾/倒産疑い）は
  default_hold＝保留へ寄せる**（D-17：不正企業＝ゼロ化への保守側）。後方互換（既定ok）。
- `screening/credibility.py::melchior_credibility_counter`：信用性スコアの危険域を MELCHIOR の
  `counter_within_domain` 用 claim に変換（M risk＝利益操作の疑い／Z risk＝倒産リスク／F risk＝健全性低）。
  **コード計算結果の摘出（R5：創作でない）・出典付き**。＝研究で再設計された **B-3のコード版**。
- `magi/persist.py::make_live_judge_fn`：`financials_fetcher`／`sector_lookup` 引数を追加（既定OFF＝
  ネット非依存・テスト注入可）。渡されたら2期財務→`assess_credibility`→
  ①MELCHIOR の counter_within_domain に信用性反証を追加（S6）②`verify(credibility_flag=)`（S5b）。
  取得失敗は graceful（ok）。`_apply_credibility` に分離。
- `orchestrator/morning_batch.py`：`run_morning_batch(financials_fetcher=None)` を追加し magi_verify ノードへ。
  `_sector_lookup`（universe の sector・業種除外用）を追加。既定OFFで既存テストはネット非依存のまま。

## 実測（受入）
- ライブ NVDA（market_cap供給）：`credibility_flag=warn`、MELCHIOR反証＝
  「利益の質に疑い（M>-1.78・売掛金/発生高）」「財務健全性が低い（F4/9＝value trap警戒）」、
  `verify` の default_hold=True（保留へ）。＝信用性warnが MELCHIOR反証と決裁ゲートに乗った。
- 全スイート green（339）。touched ファイル ruff clean。

## ハルシネ防止
- R1 信用性は全てコード計算／R5 反証は計算結果の摘出（LLM創作でない）・出典付き／
  R4 2期財務が無ければ信用性スキップ（ok・捏造しない）／業種除外で誤適用を防ぐ。

## architecture.html / spec
- §0 コミットログに「S5b/S6」行。`spec/P3_magi.md` P3-10 を ✅（コード版に再設計・配線）。

## コミット
- 本md＋defense.py＋credibility.py＋screening/__init__＋persist.py＋morning_batch.py＋
  test_credibility.py＋test_magi_persist.py＋architecture.html＋spec/P3＋progress/README を同一コミット。

## 状態/次
- 信用性(S5)が貫通経路に乗った（朝バッチで financials_fetcher=fetch_financials 指定時に有効）。
- 残り：①build_snapshot(UI)へも credibility 反映（_serialize_candidate に credibility_flag を通す小改修）
  ②S7 V字スクリーニング精緻化 ③S4b EDINET一次情報（要キー）④P6評価（R-mult記録）
  ⑤朝バッチ実運用で financials_fetcher を既定ONにする（市場別 market_cap 供給でZも有効化）。
