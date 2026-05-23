# 0031 D — screening 候補に弾を統合（信用性/V字/相対力）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で全部進めて。」のD（screening_agent への turnaround/信用性/RS 本格統合）。

## 実施内容
- `agents/screening_agent.py`：
  - `enrich_candidates(results, *, financials_fetcher, price_history)`（純粋関数）：
    上位候補（max_results に限定＝負荷限定）に
    ①信用性(S5・`assess_credibility`)→`credibility_flag`/`credibility_warnings`
    ②V字(S7a・`assess_turnaround`)→`turnaround_zone`
    ③相対力(S7c・`relative_strength_live`)→`rs_quadrant` を付与。
    **信用性warnは composite を×0.7 減点して再ランク**（粉飾/倒産疑いを上位から下げる＝D-17）。各取得失敗は graceful。
  - `ScreeningAgent.__init__(..., financials_fetcher=None, price_history=None)`：**opt-in**。
    両方渡された時のみ execute でエンリッチ（既定OFF＝ネット非依存・既存テスト不変）。
- テスト `tests/unit/test_screening_enrich.py` 4（品質付与・warn減点で再ランク・財務無しgraceful・取得失敗隔離）。

## 受入
- 全スイート green（373）。touched ファイル ruff clean。既存 screening/morning_batch テストは
  enrich OFF（既定）で不変。

## architecture.html
- §0 コミットログに「D 統合」行（S4b のハッシュ確定表記）。

## コミット
- 本md＋agents/screening_agent.py＋test_screening_enrich.py＋architecture.html＋progress/README を同一コミット。

## 状態/次（「全部進めて」の最後＝C）
- 実運用で enrich を有効化＝morning_batch の ScreeningAgent に financials_fetcher＝fetch_financials、
  price_history＝yfinance履歴 を渡す（既定ON化は朝バッチ実運用時・負荷とコストを見て）。
- 次：**C dashboard に 反証(counter)・信用性 を描画**（UI・承認済）。
