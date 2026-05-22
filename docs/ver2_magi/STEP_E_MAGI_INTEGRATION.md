# STEP E — MAGI 判断層の統合設計（既存体系への差分）

最終更新：2026-05-22
ステータス：確定（本線採用 = 選択肢3）
位置づけ：STEP A〜D 完了・Phase 1.4 まで実装済みの既存体系に対し、**MAGI / 碇司令 / GENDO を統合する差分**。
このファイルは「既存のどの設計書をどう改訂するか」を指示する**統合ハブ**。詳細は下記2ファイルに分割：
- `STEP_E_PANEL_MAGI.md` … PANEL_SPECS への追加（UI パネル仕様）
- `STEP_E_AGENT_MAGI.md` … AGENT_SPECS への改訂（market-analyst の MAGI 化）

> 既存ドキュメント（PANEL_SPECS / AGENT_SPECS / SYSTEM_DESIGN / README_INDEX）の確定事項と
> 衝突する点は本書 §3 にすべて列挙し、更新指示を出す。code側は本書を読んでから該当設計書を改訂する。

---

## 1. なぜこの差分が必要か（背景）

既存設計は market-analyst が **5軸スコアを1つの総合スコア（buy_signals.score 0-100）に統合**して出す。
買い推奨カードも「総合スコアの5軸」で銘柄を要約していた（PANEL_SPECS 1.3）。

本差分は、この「統合して1点にする」最終ステップを廃し、**3審判（MAGI）が独立判定し、総合スコアを出さない**形に上書きする。
理由：単一スコアは「3軸を統合してないのに統合したフリ」で、ユーザーの思考停止（ラバースタンプ化）を招く。
判断はMAGI（独立検証）、推奨は碇司令、決裁は人間、という3層に再編する。

---

## 2. 本線の方針（採用 = 選択肢3）

**裏（計算ロジック）は既存資産を流用、表（出力と表示）を三権独立に再編する。総合スコアは出さない。**

### 2.1 5軸 → 3審判のマッピング
既存の5軸算出式（`calculate_fundamental_score` 等）は**捨てずに流用**し、3審判に再編する。

| 既存5軸 | → | MAGI 審判 | 判断軸 |
|---|---|---|---|
| fundamental_score | → | MELCHIOR・1 | 業績（ファンダ） |
| technical_score | → | BALTHASAR・2 | 株価（テクニカル） |
| news_sentiment_score + strategy_fit_score | → | CASPER・3 | 文脈（イベント・テーマ） |
| ai_confidence | → | （統合スコアにせず）碇司令の総合判断材料へ | — |

### 2.2 変えること / 変えないこと
| | 内容 |
|---|---|
| 変えない | 各軸の計算式（fundamental/technical 等の純粋関数）、MCPツール、データ取得 |
| 変える | ①5軸を1つの総合scoreに合算する処理を**廃止** ②buy_signals の出力形式（後述スキーマ） ③買い推奨カードUI（総合スコア→GENDO+3審判並置） |
| 新規追加 | 碇司令（commander_rec）、検証フラグ、GENDO stance、3審判の verdict 保存 |

### 2.3 三権独立の原則（AGENT_SPECS 改訂の核）
- 3審判は同じ銘柄を、互いの結論を見ずに、独立に「verdict + reason + confidence」を出す。
- **反証専任は置かない**（割れること自体が反対意見）。検算はコードが機械的に行う。
- 統合機構は判断しない（割れ方を可視化するのみ・総合スコアを出さない）。
- 碇司令は MAGI 出力**だけ**を根拠に推奨＋反対論拠を出す。新事実の創作禁止（機械チェック）。

---

## 3. 既存確定事項の更新指示（衝突点の解消）

> README_INDEX「確定済みの重要決定リスト」と矛盾する点を、ここで明示的に上書きする。

### 3.1 【上書き】明朝・セリフ完全禁止 → 限定解除
- 旧確定：「✅ 明朝・セリフ完全禁止」（README_INDEX）
- 新確定：**原則ゴシック（Noto Sans JP）。明朝（Noto Serif JP）は以下3箇所のみ許可**：
  ① MAGI 審判の固有名（MELCHIOR・1 等） ② 碇司令の名前 ③ 碇司令の発言本文（推奨＋反対論拠）
- 理由：碇の「人格を持つ判断者」性をフォントで表現し、MAGIの無機質な事実提示と対比させるため。
- 実装注意：両 Noto を必ず読み込む。ゴシックを継承任せにしない（セリフへフォールバックし全面明朝化する既知の罠）。

### 3.2 【上書き】買い推奨カード = 総合スコアの5軸 → GENDO + 3審判
- 旧確定：PANEL_SPECS 1.3「総合スコアの5軸、3シナリオ生成」
- 新確定：総合スコア表示を廃止。GENDOインジケーター + MAGI 3審判（業績/株価/文脈）の並置に置換。
- 詳細は `STEP_E_PANEL_MAGI.md`。

### 3.3 【上書き】market-analyst = 5軸統合 → MAGI 3審判
- 旧確定：AGENT_SPECS §2「5軸スコアと3シナリオを出力」
- 新確定：5軸を3審判に再編、総合スコアを出さず、碇司令を後段に追加。
- 詳細は `STEP_E_AGENT_MAGI.md`。

### 3.4 【追加】データモデル：buy_signals 拡張 + 新規テーブル
- buy_signals：`score`(総合) を**廃止 or 非表示**。代わりに gendo_stance を追加。
- 新規 `judge_verdicts`：審判ごとの判定（後述）。
- 新規 `commander_recs`：碇司令の推奨。
- 新規 `verifications`：検証フラグ。
- 詳細スキーマは `STEP_E_AGENT_MAGI.md` §データモデル。

### 3.5 【変更なし・確認】以下は既存のまま維持
- Core-Satellite、moomoo、Tier 1 手動発注、Hot/Cold Path、予算（日500/月5000）、
  6エージェント構成（market-analyst の中身のみ MAGI 化、他5体は不変）、売り買い同等の主役、
  利確半量/損切り全量、目標期間グラフ、3シナリオ思考。

---

## 4. 実装への影響と順序

### 4.1 影響範囲（Phase 1.4 まで実装済みに対して）
- market-analyst（実装済み 1.4.3）：**改訂が必要**。5軸合算→3審判出力へ。
- buy_signals スキーマ：マイグレーション必要（score 廃止、gendo_stance 追加、新規3テーブル）。
- UIプレビュー（稼働中）：買い/売りカードの表示を GENDO+3審判へ。
- 他5エージェント・MCPツール・計算式：**流用（変更最小）**。

### 4.2 推奨実装順序（既存 IMPLEMENTATION_PHASES に挿入）
本差分は Phase 1.6（UI Next.js）の前に、エージェント側を先に整える：
1. データモデル拡張（judge_verdicts / commander_recs / verifications、buy_signals 改訂）
2. market-analyst を MAGI 3審判出力に改訂（既存計算式は流用）
3. 碇司令ロジック（commander_rec 生成 + MAGI 準拠チェック）
4. 検証フラグのコード判定（数値照合 / 信用性 / 碇 MAGI 準拠）
5. UI（買い/売りカード + 詳細パネル）を GENDO+MAGI+碇に再構築 ← dashboard.html が参照見本

### 4.3 既存テストへの影響
market-analyst のテスト（5軸前提）は書き直し。3審判が独立判定し総合スコアを出さないことを検証する形へ。

---

## 5. 参照見本
- `dashboard.html`（本日版）：GENDOインジケーター + MAGI決裁ブロック + 碇司令ゾーンの完成イメージ。
  コピー元ではなく、Next.js 再構築時の見た目の正とする。

---

## 6. このファイル群の位置づけ（README_INDEX への追記指示）
README_INDEX の「STEP D」の下に「STEP E：MAGI 統合」を追加し、本3ファイルを登録すること：
- STEP_E_MAGI_INTEGRATION.md（本書・統合ハブ）
- STEP_E_PANEL_MAGI.md（UI パネル差分）
- STEP_E_AGENT_MAGI.md（エージェント差分）
