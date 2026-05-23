# 0023 S5（弾）— 信用性フィルタ Beneish M / Piotroski F / Altman Z

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「決済はmoomooの画面で行うから後でいい。それ以外で実装すべきことから進めて。」（研究の順で 餌→弾）。

## 判断
S4a（2期財務）が入ったので、研究 領域1 の信用性3手法（D-17＝不正企業ゼロ化への最重要防御）を実装。
**3手法ともコード計算・LLM不使用**（原則4）。一致を求めない独立warn・ソフト警戒（誤検出あり）・UIは定性ゾーン。

## 実施内容（S5）
- 新規 `trading_agent/screening/credibility.py`：
  - `beneish_m_score`：8比率（DSRI/GMI/AQI/SGI/DEPI/SGAI/TATA/LVGI）。M>-1.78=risk・-2.22<M<-1.78=grey。
    主要変数欠損は na（埋めない）。
  - `piotroski_f_score`：9項目（収益性4/レバ流動性3/効率2）を0/1加点。7-9=safe・5-6=grey・0-4=risk。判定可能項目<5はna。
  - `altman_z_score`：5比率（WC/RE/EBIT/MktCap/Sales÷TA系）。Z>2.99=safe・1.81-2.99=grey・<1.81=risk。要market_cap。
  - `assess_credibility`：3手法を独立評価→`credibility_flag`(ok/warn)。**どれか1つでもrisk→warn**（一致不要）。
    金融/REITはM/Z除外（業種注意）。F-Scoreは質（MELCHIOR/value trap）にも寄与。
  - **数値はコード（R1）／欠損na（R4）／UIに数値は出さない（SCORE:NONE思想・内部スコア→定性ゾーン）**。
- `screening/__init__.py` で公開。テスト `tests/unit/test_credibility.py` 12
  （M：clean safe/manipulator risk/1期na、F：strong safe/weak risk/欠損na、Z：healthy safe/distressed risk/cap欠na、
   集約：clean ok/risk1つでwarn/金融除外）。

## 実測（受入・ライブ NVDA）
- `credibility_flag=warn`。M=risk(-1.13)＝**高成長(売上130B→216B)でSGI/DSRIが上振れ→ソフト警戒**
  （研究の指摘どおりM-Scoreは急成長で誤検出しやすい＝ハード除外でなくwarnが正しい挙動）。
  F=4/9(risk)、Z=na（本ライブ呼びでmarket_cap未指定のため。実運用は.infoから供給）。
- 全スイート green、touched ファイル ruff clean。

## architecture.html
- §0 コミットログに「S5（弾）」行を追加（S4aハッシュ確定表記）。

## コミット
- 本md＋screening/credibility.py＋screening/__init__.py＋test_credibility.py＋architecture.html＋progress/README を同一コミット。

## 状態/次
- 残り（弾の配線・接続）：
  ①**S5b 配線**：`assess_credibility` を MAGI フロー（defense.py の credibility_flag・persist の judge材料）へ接続。
   ＝Financials を materialize/magi_verify に渡し、`Verification.credibility_flag` を実値化（現状"ok"固定）。
  ②**S6 MELCHIORコード反証**：M-Scoreの高変数（DSRI/TATA/AQI）を「利益の質への疑い」として
   `melchior` の `counter_within_domain` にコード摘出（B-3のコード版・LLM不要）。
  ③S7 V字スクリーニング精緻化。④S4b EDINET一次情報（要キー）。⑤P6評価。
