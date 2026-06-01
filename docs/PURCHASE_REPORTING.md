# 購入状況の報告方法（Claude Code 対話 + CLI）

楽天証券で実際に発注・約定したら、以下の方法で **Claude（私）に伝える** か、**直接 CLI を叩く** ことで DB に反映できます。

---

## 方法 A: Claude に自然言語で伝える（推奨）

Claude Code セッション内で以下のように発言してください：

### 例 1: 単純な報告
```
3697 を 14 株 ¥702 で買った
```

私が解釈：
1. Decision テーブルから ticker=3697 の awaiting を検索
2. 確認: 「SHIFT (3697) を 14 株 × ¥702 = ¥9,828 で fill (live モード) ですか?」
3. ユーザー承認後、`mark_filled.py` を実行
4. DB に Portfolio エントリ作成、Decision を filled に
5. 結果報告

### 例 2: 複数銘柄まとめて
```
今日の発注完了
3697 14株 702
4587 9株 1107
4733 1株 6117
```

私が一括処理：
1. 各行を解釈
2. 一覧で確認: 「以下を fill しますか? (合計 ¥27,008)」
3. 承認後、各 Decision について mark_filled 実行

### 例 3: 約定価格が違った場合
```
3697 14株 ¥720 (寄付スリッページで想定より高くなった)
```

私が解釈：
- 寄付想定は ¥702 だったが、実約定 ¥720 で記録
- mark_filled で `--price 720` を指定

---

## 方法 B: 直接 CLI を実行

```bash
# 楽天本番として記録（デフォルト）
.venv/bin/python scripts/mark_filled.py --decision-id 87 --price 702 --shares 14

# ticker 指定（今日の awaiting を自動検索）
.venv/bin/python scripts/mark_filled.py --ticker 3697 --price 702 --shares 14

# 試験運用として記録（broker_mode=paper）
.venv/bin/python scripts/mark_filled.py --decision-id 87 --price 702 --shares 14 --broker-mode paper

# DS 機別の検証（personality 指定）
.venv/bin/python scripts/mark_filled.py --ticker 3697 --price 702 --shares 14 --personality ASUKA
```

---

## broker_mode の使い分け

| broker_mode | 用途 | 損益 |
|---|---|---|
| **`live`**（デフォルト） | 🟢 **楽天証券で実発注したケース** | 実弾 |
| **`paper`** | 🟡 試験運用・ペーパー検証 | 仮想 |

朝バッチは並行運用：
- 試験運用（paper）は朝バッチで自動更新
- 楽天本番（live）はユーザーの mark_filled で記録

両者は同じ DB だが `broker_mode` フィールドで分かれ、評価集計も別々に。

---

## 私が行う確認プロセス

ミスを防ぐため、私は **必ず確認** してから DB を更新します：

```
ユーザー: 3697 14株 ¥702 買った

私: 確認させてください：
   - 銘柄: 3697 SHIFT Inc.
   - 株数: 14 株
   - 価格: ¥702 / 株
   - 合計: ¥9,828
   - broker_mode: live（楽天本番）
   - 該当 Decision: id=87
   
   この内容で DB に記録してよいですか？

ユーザー: はい

私: [mark_filled CLI 実行]
   ✓ Decision id=87 ticker=3697 を filled に更新（🟢 楽天本番）
   Portfolio id=123 作成（残予算: ¥90,172）
```

---

## エラー対応

### Decision が見つからない場合
```
ユーザー: 1234 5株 ¥500 買った

私: ticker=1234 の今日の awaiting Decision が見つかりません。
   以下の可能性があります：
   - 銘柄コードの入力ミス
   - 推奨リストにない銘柄を独自判断で発注
   
   独自発注として記録しますか? （Decision なしで Portfolio のみ作成）
```

### 寄付不約定の場合
```
ユーザー: 3697 は寄付で約定しなかった

私: 該当 Decision はそのまま awaiting で残します。
   翌朝バッチで自動的に再評価され、まだ候補なら新規 Decision として継続、
   候補から外れていれば cancel されます。
```

---

## 推奨ワークフロー（朝の運用）

```
1. 07:00 launchd 朝バッチ自動実行
2. 08:30 iPhone で発注リスト HTML を開く
3. 08:45-08:55 楽天証券アプリで順次発注
4. 09:00 寄付で約定
5. 09:30 Claude Code を起動して報告:
   "今朝の発注完了
    3697 14株 702
    3994 2株 4343
    4587 9株 1107
    ..."
6. 私が一括で mark_filled 実行 → DB 反映
7. 結果サマリ表示（合計約定額・残予算・新規 Portfolio 件数）
```

---

## DB 構造（参考）

| テーブル | broker_mode 別 |
|---|---|
| `Decision` | broker_mode 中立（共通の買い候補） |
| `Portfolio` | broker_mode 別（paper / live が混在しない） |
| `MisatoTreasury` | broker_mode 別（試験運用と楽天本番で独立） |
| `PilotAllocation` | broker_mode 別 |

これにより試験運用と楽天本番が **完全に独立** して並行運用されます。
