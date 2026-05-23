# 0026 build_snapshot に信用性を反映（弾を画面で見えるように）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「進めて。」（推奨＝build_snapshot へ credibility 反映、または S7。本コミットは前者）。

## 実施内容
- `scripts/build_snapshot.py`：
  - `_credibility_flag(ticker, verdicts)`：2期財務（`fetch_financials`・market_capは `_market_cap`＝fast_info）→
    `assess_credibility` → `melchior_credibility_counter` を MELCHIOR の counter_within_domain に付与し、
    credibility_flag を返す（live のみ・失敗はok・graceful）。
  - `_serialize_candidate(..., credibility_flag=)`：`verify(verdicts, credibility_flag=)` に渡す。
    judges 出力に **`counter`（各審判の反証claim配列）** を追加。
  - `_build_candidates`：CASPER格上げ後に `_credibility_flag` を計算して直列化へ。
- これで snapshot（UIが読む `/data/snapshot.json`）に信用性が乗る。

## 実測（ライブ・NVDA）
- snapshot に：MELCHIOR `counter`＝「利益の質に疑い（M>-1.78…）」「財務健全性が低い（F4/9＝value trap）」、
  verification の **「信用性」flag = warn**、`default_decision = 保留`、
  碇の counter にも信用性反証が集約（B-4）。＝**弾が画面データに出た**。
- 全スイート green、build_snapshot は ruff clean。

## 留意（フロント側の残り）
- snapshot には `counter` が入ったが、**dashboard側の描画（LiveData.tsx 等）で counter を表示する改修は別途**。
  既存の「信用性」flagは ok→warn が反映される（既存描画）。counter リスト表示は次のUI微修正で。

## architecture.html
- §0 コミットログに「UI反映」行（S5b/S6 のハッシュ確定表記）。

## コミット
- 本md＋build_snapshot.py＋architecture.html＋progress/README を同一コミット。
  （snapshot.json は生成物＝gitignore）

## 状態/次
- 残り：①dashboard で counter（反証）を描画 ②S7 V字スクリーニング ③S4b EDINET（要キー）④P6評価
  ⑤朝バッチ実運用で financials_fetcher 既定ON。
