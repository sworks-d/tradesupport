# 0028 S7b（弾）— screening の V字スコアに value trap ガード（Value×Momentum）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「進めて」（推奨の screening への V字統合へ）。

## 判断（既存スコアの欠陥を研究で修正）
`mcp_tools/screening.py::calculate_v_shape_score` は V字4軸を持つが、**株価底打ち(30pt)を業績反転と
無関係に加点**していた＝研究 領域3-A が警告する **value trap**（底にいるだけで高得点）。
研究の核心「②点火と③株価転換が揃って初めて合格／①底だけは除外」「Value×Momentum両立(Asness)」を反映。

## 実施内容
- `calculate_v_shape_score`：
  - **ignition（反転の点火）** を明示：赤字→黒字／減益→大幅増益のみ点火（**単なる増収は点火でない**）。
  - **株価底打ちは点火がある時のみ満額30**。点火なしで底だけ＝**10に割引＋`value_trap=True` フラグ**
    （Value×Momentum両立：勢い＝点火 と 割安＝底 のANDで初めて満額）。
  - 既存の「赤字→黒字＋底＋テクニカル＋出来高＝85」は点火ありなので不変（後方互換）。
- テスト `tests/unit/test_screening.py` +3（点火なし底＝10/value_trap、点火あり底＝満額55、増収は点火でない＝20）。

## 効果
- 「底にいるだけ」の銘柄が composite を稼いで候補化される穴を塞いだ。
  点火（業績反転）を伴う真のV字だけが底のフルスコアを得る＝候補の質が上がる。

## 受入
- 全スイート green（349）。touched ファイル ruff clean。既存 combo テスト（85）不変。

## 留意（S7の残り）
- 本コミットは**既存スコア関数への value trap ガード**（contained・後方互換）。
  `screening_agent` から **S7a の `assess_turnaround`／S5 `assess_credibility`（生存性）を2期財務で本格統合**するのは別途
  （ScreeningResult への credibility/turnaround フィールド追加＋agentでの財務取得が要る）。
  **テーマ相対力（対S&P 3-6ヶ月・4象限・Improving）** も別途（指数/構成銘柄の価格データが要る）。

## architecture.html
- §0 コミットログに「S7b（弾）」行（S7a のハッシュ確定表記）。

## コミット
- 本md＋mcp_tools/screening.py＋test_screening.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 残り：①screening_agent への turnaround/credibility 本格統合 ②テーマ相対力 ③P6評価 ④S4b EDINET（要キー）
  ⑤dashboard で counter 描画（UI変更＝要ゲート）⑥朝バッチ financials_fetcher 既定ON。
