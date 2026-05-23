# 0030 S4b — 開示（TDnet/EDINET）の信用性レッドフラグ（D-14 第1フィルタ）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で全部進めて。」のB（EDINET一次情報）。

## 判断（キー無しでも効く第1フィルタを先に）
EDINET の**書類一覧取得（`_fetch_edinet`）は既存**（キー無しはgraceful空）。S4b の本丸は
「監査意見/GC注記/上場廃止/特設注意」を**信用性のレッドフラグ**にすること（D-14 第1フィルタ・D-17 最重要）。
XBRL本文の深掘り照合は要EDINETキー＋重実装だが、**開示メタデータ（タイトル/説明）のキーワード走査**は
キー無し（TDnet RSS）でも効く第1段として価値がある → これを先に実装。

## 実施内容
- `screening/credibility.py`：
  - `scan_disclosure_red_flags(disclosures)`：開示のタイトル/説明から D-14 レッドフラグを抽出（コード・LLM不使用）：
    上場廃止/特設注意市場/監理銘柄/継続企業の前提(GC)/不適正意見/意見不表明/限定付適正意見/
    内部統制の重要な不備/有報の訂正/課徴金/不適切な会計/粉飾。**通常開示（業績予想修正等）は拾わない**。
  - `assess_credibility(fin, *, sector=None, disclosures=None)`：開示レッドフラグも warn に寄与。
    `CredibilityResult.disclosure_flags` を追加。`melchior_credibility_counter` が開示フラグも反証に出す。
- `screening/__init__.py` で公開。テスト `tests/unit/test_credibility.py` +3
  （GC/訂正検出・通常開示は非検出・健全財務でも開示GCで warn＋MELCHIOR反証）。

## 受入
- 全スイート green（368）。touched ファイル ruff clean。

## 留意（残り）
- 開示を**ライブのMAGI経路に流す**配線（make_live_judge_fn/build_snapshot で disclosure 取得→
  assess_credibility(disclosures=) に渡す）は次段（Dの統合 or 小追補）。本コミットは走査＋集約ロジック。
- **XBRL深掘り**（GC注記本文の照合・監査意見の正確抽出）は**要EDINETキー**の将来拡張。

## architecture.html
- §0 コミットログに「S4b 開示信用性」行（S7c のハッシュ確定表記）。

## コミット
- 本md＋screening/credibility.py＋screening/__init__＋test_credibility.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 次（全部進めて）：D＝screening_agent 統合（turnaround/信用性/RS/開示フラグを実スクリーニングに）→ C＝dashboard描画。
