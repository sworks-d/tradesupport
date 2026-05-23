# 0040 価格ベース・バックテスト基盤（資産が増えるかの最速検証）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「口座以外の部分を動かして検証して。実際に資産が増えるのかテストしたい。どのフェーズでできるか教えて」。

## 回答（フェーズ）と本コミットの位置づけ
- 口座以外のパイプラインは**ライブ実行で検証済**（universe→MAGI→decision→発注リスト・¥100k・実価格）。
- 「資産が増えるか」の検証は3段：A判断妥当性（今すぐ）/ **B バックテスト（基盤を作れば数日・速い）** /
  C 前向きペーパー（数週〜数ヶ月・不偏）/ D 実弾（増額ゲート後）。
- 最速のBを実装。**ただし look-ahead 回避のため価格ベースに限定**（財務=MELCHIORは point-in-time が要る＝
  現財務を過去判断に使うと先読みバイアス＝研究領域4の罠。価格は先読み回避可）。

## 実施内容
- 新規 `trading_agent/evaluation/backtest.py`：
  - `backtest_signal(prices, signal_fn, hold_bars, stop_pct, target_return, warmup)`：
    各時点 i の判断は `prices[:i+1]` のみ（**先読みなし**）。シグナル→エントリー、target/stop/期間満了で手仕舞い、
    **重複建てしない**。`Trade` 列＋`build_track_record` で R-multiple/hit-miss/命中率/平均R を返す。
  - `sma_cross_signal(20,60)`：ゴールデンクロス（先読みなし）。
- 新規 `scripts/run_backtest.py`：universe 全銘柄を再生し合算 Track Record（`--period/--hold/--stop/--target`）。
  **生存者バイアス・単一局面の粗指標**であることを明示（将来を保証しない）。
- テスト `tests/unit/test_backtest.py` 7（target到達hit/stop到達miss/重複なし/warmup/シグナル無し/GC発火/履歴不足）。

## 実測（ライブ・実株価・先読みなし）
- NVDA 3年・GC20/60・hold60・stop12%・target20%：trades=5・命中率1.0・平均R+1.30・平均リターン+15.6%
  （NVDAは強い上昇＝高命中は当然。小サンプル＝暫定。machinery は正しい＝先読みなし）。
- 全スイート green（396）。touched ファイル ruff clean。

## 正直な限界
- 現universe・単一期間＝**生存者バイアス**。財務込みの完全バックテストは point-in-time データが要る（重い・後段）。
- これは「実弾前に規律の期待値を粗く見る」用途。確度はC（前向きペーパー）が最も高い。

## architecture.html
- §0：XBRL深掘り行のハッシュ確定＋「バックテスト」行。

## コミット
- 本md＋evaluation/backtest.py＋evaluation/__init__＋scripts/run_backtest.py＋test_backtest.py＋architecture.html＋progress/README を同一コミット。

## 状態/次（自走の続き）
- 残り自走：MELCHIOR accrual反証 / 発注フック(P5-3) / XBRL自動引き当て / spec同期。
