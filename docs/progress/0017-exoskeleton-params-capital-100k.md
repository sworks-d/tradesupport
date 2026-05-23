# 0017 規律層（外骨格）§1 — リスク数値の整合版確定＋元本¥100k

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「doc/researchを確認して」→ 3本（RESEARCH_METHODS / RESEARCH_TO_IMPLEMENTATION / RISK_EXOSKELETON）を精読。
- 選択：①次の着手＝「外骨格数値→S3出口」 ②universe＝「日本株主体に再編」 ③リスク8数値＝「整合版を私が出す」。

## 判断（研究の course-correction）
- 研究の鉄則「**貫通→餌→弾**」に乗り換え。直近のB群（反証層）は第3段＝弾を先取りしていた。
- **B-3はLLMでなくコード反証に再設計**（研究：反証は大半コード計算可能・MELCHIOR反証は2期財務=S4前提）
  → 着手していた `reflection.py`(LLM版)は削除。MELCHIORコード反証はS4後。
- 外骨格（リスク規律）は「数値はコード」原則と整合し、MAGIの外側に被せる規律層として実装可。

## 実施内容（§1：数値確定＋元本）
- **元本 ¥1M→¥100,000**：`brokers/standin.py` の `STARTING_CASH_JPY=100_000.0`。snapshot のコメントも更新。
  `tests/unit/test_brokers.py` の期待値を¥100kに。
- **規律層パラメータの単一の正**：新規 `trading_agent/risk/params.py`（`RiskParams`/`DEFAULT_RISK`）。
  RISK_EXOSKELETONの8数値を**制約系として整合**：
  ①2%(¥2,000=1R・口座増で逓減) ②同時保有**5**（"5–8"を物理整合：投資可能¥80k÷R-mult¥13–20k＝4–6）
  ③1テーマ2銘柄かつ30% ④現金下限20% ⑤DD−15%停止 ⑥増額ゲート(30decision/両局面/DD−15%/平均R>0)
  ⑦損切り10–15%(既定12%) ⑧日本株主体(別§でuniverse再編)。`one_r_jpy`＝anti-martingale(残高比固定%)。
- 仕様 `docs/plan/spec/G_risk_discipline.md`（G-0確定値＋G-1〜G-6予定）。`DECISIONS.md` D-23。
- テスト `tests/unit/test_risk_params.py` 6（¥100k×2%=¥2,000／anti-martingale縮小／stop10%=20%上限の噛み合い／
  ②の銘柄数が現金下限と閉じる／既定stopが帯内／不変条件）。

## 整合の要（②の数学＝率直な指摘の反映）
doc「5〜8銘柄」は ¥100k＋20%上限＋現金20%下限と数値的に両立しない。R-multポジション=1R÷stop%＝
stop10%で¥20,000(=20%上限)、stop15%で¥13,333。投資可能¥80k÷これ＝4〜6銘柄 → **上限5**に整合した。

## 受入
- 全スイート green。`risk/` と test は ruff clean。

## architecture.html（コミットとセット）
- §0 進捗：方針転換（貫通→餌→弾・規律層）の note ＋ コミットログに「外骨格§1」行を追加。

## コミット
- 本md＋standin.py＋risk/(params.py,__init__)＋test_risk_params.py＋test_brokers.py＋build_snapshot(コメント)
  ＋spec/G_risk_discipline.md＋DECISIONS＋architecture.html＋progress/README を同一コミット。

## 状態/次
- §2：universe を**日本株主体**に再編（load_universe.py）。
- §3：`recommend_position` を R-mult（stop逆算）＋20%上限＋現金下限に拡張。
- §4：集中（テーマ/セクター≤2・≤30%）・現金≥20%・DD−15%新規停止の規律ノード。
- S3：A-5 決裁→発注リスト（規律を効かせた出口＝最初の貫通完成）。
