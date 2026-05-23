# 0032 C — dashboard に反証(counter)を描画（UI）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で全部進めて。」のC（dashboard で反証・信用性を描画）。
  → これは UI 変更（D-19 UI変更ゲート）だが、本指示で**承認**されたものとして実施。

## 実施内容
- `ui/app/LiveData.tsx`：
  - `JudgeMini` 型に `counter?: string[]`（自領域の反証）を追加。
  - 詳細パネルの各審判行 `.magi-jreason` に、反証があれば「　／反証：…」を**併記**
    （**新要素は増やさず**既存の根拠文に追記＝レイアウトを変えない＝「狂いなく再現」を尊重）。
  - 信用性(credibility)は既存の verification flags 描画（`.magi-flag`）で warn 表示されるため追加改修不要。
- snapshot（build_snapshot）は既に judges[].counter／信用性flag を出力済（commit 2d57e08）。

## 受入
- `npx tsc --noEmit` グリーン（型安全）。Pythonスイートは不変（UIのみの変更）。
- 反証の併記は textContent（innerHTML不使用）＝注入安全。

## 留意
- 反証を独立要素（バッジ/折りたたみ）で見せる凝った描画は今後のUI調整余地（本コミットは最小・非破壊）。
- 朝バッチ/snapshot で enrich/credibility を実データ供給する運用ON化は別途（負荷・コスト見て）。

## architecture.html
- §0 コミットログに「C UI描画」行（D のハッシュ確定表記）。

## コミット
- 本md＋ui/app/LiveData.tsx＋architecture.html＋progress/README を同一コミット。

## 状態（「全部進めて」完了）
- A(テーマ相対力)・B(開示信用性)・D(screening統合)・C(UI描画) を実施。
- 残り（要あなた）：①EDINETキー設定→S4b深掘り ②朝バッチで enrich/credibility/financials を既定ON
  ③P6評価（データ蓄積後）④反証の凝ったUI描画。
