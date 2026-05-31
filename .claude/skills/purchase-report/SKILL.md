---
name: purchase-report
description: 楽天証券での実発注報告を受け取り、DB に反映する。試験運用（paper）と楽天本番（live）の自動振り分け。並列で複数銘柄まとめて報告も可能。
---

# 楽天購入報告スキル

INVESTIGELION の朝バッチが生成した発注リスト ([autoreport/orders/](../../../autoreport/orders/)) に従って、ユーザーが楽天証券で実発注した結果を受け取り、`scripts/mark_filled.py` で DB に反映するスキル。

## このスキルの目的

- **連携漏れ防止**：DB が単一の真実源。ユーザーの手動発注と DB 状態を同期する唯一の経路
- **ミス防止**：必ず確認画面を出してからユーザー承認後に実行
- **試験運用 (paper) と楽天本番 (live) の自動振り分け**：デフォルトは live、明示で paper

## 受け取る入力形式

ユーザーは以下のいずれかの形式で報告：

### 形式 1: 1 行コマンド型
```
3697 14株 ¥702
```
または
```
3697 14 702
```

### 形式 2: 複数行リスト型（推奨）
```
今朝の発注完了
3697 14株 702
3994 2株 4343
4587 9株 1107
4733 1株 6117
```

### 形式 3: 自然文
```
3697 を 14 株、寄付 ¥702 で買いました
```

### 形式 4: 試験運用として記録（明示）
```
試験運用として記録
3697 14株 702
```
または末尾に `(paper)` を付加

## 処理手順

1. **入力解析**：各行から `(ticker, shares, price)` のタプルを抽出
   - 「株」「¥」「,」「円」等は除去
   - 数値以外は無視
   - 「試験運用」「paper」キーワードがあれば `--broker-mode paper`、なければ `live`

2. **Decision 検索**：各 ticker について、`Decision.date == today_jst() AND status == 'awaiting'` を検索
   - 見つからない場合は「独自発注として記録しますか?」と聞く

3. **確認画面表示**：
   ```
   📋 以下を fill します (broker_mode=live = 楽天本番)
   
   | # | ticker | 銘柄名 | 株数 | 価格 | 合計 |
   |---|---|---|---|---|---|
   | 1 | 3697 | SHIFT | 14 | ¥702 | ¥9,828 |
   | 2 | 3994 | Money Forward | 2 | ¥4,343 | ¥8,686 |
   ...
   
   合計約定額: ¥XX,XXX
   現在の live Treasury 残: ¥XX,XXX
   fill 後の残予算: ¥XX,XXX
   
   実行してよいですか？ (y/n)
   ```

4. **ユーザー承認後**、各銘柄について実行：
   ```bash
   .venv/bin/python scripts/mark_filled.py --ticker {ticker} --price {price} --shares {shares} [--broker-mode paper]
   ```

5. **結果サマリ報告**：
   ```
   ✓ X 件 fill 完了
   
   - 3697 SHIFT: ¥702 × 14 = ¥9,828 → Portfolio id=N
   - 3994 Money Forward: ¥4,343 × 2 = ¥8,686 → Portfolio id=M
   ...
   
   合計: ¥27,008
   残予算 (live): ¥72,992
   新規 Portfolio: X 件
   
   翌朝バッチで未完了 Decision は自動 cancel されます。
   ```

## エラー対応

### Decision が見つからない場合
```
⚠️ ticker=1234 の今日の awaiting Decision が見つかりません。
   以下の可能性:
   - 銘柄コード入力ミス（楽天での発注時に正しいコードでしたか?）
   - 推奨リストにない銘柄を独自判断で発注した
   
   独自発注として記録しますか?
   - Yes: Decision なしで Portfolio のみ作成（broker_mode=live）
   - No: スキップ
```

### 価格・株数の不正
```
⚠️ 解析できない行: "{line}"
   形式例: 3697 14株 ¥702
   このまま続けるか、修正版を入力してください
```

### 一部失敗
- 成功分は反映、失敗分は明示
- 「3 件成功、1 件失敗（理由付き）」とサマリ

### Treasury 残不足
```
⚠️ 合計約定額 ¥150,000 が live Treasury 残 ¥100,000 を超えています。
   楽天で本当に発注しましたか? Treasury 残高との差は:
   - 入金漏れの可能性
   - 信用取引等の特殊ケース
   
   このまま記録しますか?
```

## 重要な仕様

- **broker_mode のデフォルトは `live`**（楽天本番想定）。`paper`（試験運用）は明示が必要
- **必ず確認画面を出す**（連携漏れ防止）
- **複数銘柄の一括処理対応**（朝の発注完了報告に最適）
- **Decision なしの独自発注**にも対応（Portfolio のみ作成）
- **mark_filled CLI を使う**（直接 SQL UPDATE は禁止 / 既存ロジック尊重）

## 関連ドキュメント

- [docs/PURCHASE_REPORTING.md](../../../docs/PURCHASE_REPORTING.md) — 詳細な使用方法
- [scripts/mark_filled.py](../../../scripts/mark_filled.py) — 実行 CLI
- [trading_agent/reporting/order_list.py](../../../trading_agent/reporting/order_list.py) — 発注リスト生成

## メモリとの関係

- [[project_system_purpose]] — システム全体の目的（少額〜本番スケール、楽天本番運用）
- [[feedback_no_arbitrary_data_mutation]] — 独自 SQL UPDATE 禁止、既存ロジック (mark_filled) 使用
- [[feedback_dont_declare_complete_with_critical_left]] — 連携漏れがないか必ず確認
