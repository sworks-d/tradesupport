# 0029 S7c（テーマ）— 相対力（Relative Strength）＋4象限

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で全部進めて。」（A テーマ相対力／B EDINET／D screening統合／C dashboard描画 を全部）。本コミットはA。
  - 市場proxyは私の既定で：US=^GSPC（S&P500）・JP=^N225（日経225）。

## 実施内容（研究 領域3-B）
- 新規 `screening/relative_strength.py`：
  - `compute_relative_strength(ticker_prices, market_prices, short=21, long=63)`：
    rs＝銘柄リターン−市場リターン。long で強弱・short で改善/悪化を測り**4象限**に分類：
    Leading（◯◯）／Weakening（◯✕）／Lagging（✕✕）／**Improving（✕◯＝底から改善＝テーマV字）**。
  - `market_proxy(market)`：US=^GSPC／JP=^N225（既定）。`relative_strength_live(ticker, market, history=)`：
    銘柄とproxyの価格を引いて算定（注入可能・失敗はna）。
  - 数値はコード（R1）・履歴不足はna（R4）。**役割分担**：方向＝相対力（コード）、理由＝CASPER（文脈）。
    高回転を避ける中期前提＝判定は「エントリーの追い風」に使う。
- テスト `tests/unit/test_relative_strength.py` 7（leading/lagging/improving/履歴不足na/proxy選択/
  取得失敗na/銘柄とproxyの両取得）。

## 受入
- 全スイート green（353）。touched ファイル ruff clean。

## architecture.html
- §0 コミットログに「S7c テーマ」行（S7b のハッシュ確定表記）。

## コミット
- 本md＋screening/relative_strength.py＋screening/__init__＋test_relative_strength.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 次（同じ「全部進めて」内）：B＝S4b EDINET provider（キー無しgraceful）→ D＝screening_agent統合 → C＝dashboard描画。
- テーマ相対力の screening_agent への組込み（テーマ4軸の Improving 加点）は D で行う。
