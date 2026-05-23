# 0042 spec/00_overview 同期（doc drift 解消）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「自走部分は完走して」。最後の自走項目＝spec同期。

## 背景
architecture.html §0 は毎コミット更新していたが、`docs/plan/spec/` の進捗マーカーが古いまま
（実装済の多くが ❌ のまま）だった。設計の正典 spec を実装状態に合わせる。

## 実施内容
- `docs/plan/spec/00_overview.md` §7 INDEX を全面同期（最終同期2026-05-23・progress 0009〜0041 反映）。
  P1-4✅/P1-5🟡/P1-6✅/P1-7✅、P2全て✅、P3-1/3/4/5/6✅・P3-7🟡、P4-3/4✅、P5-2✅/P5-3🟡、
  P6-1/2✅・P6-3❌、規律層(外骨格)✅、実行入口一覧 を追記。
- 各Pヘッダの明確に古い ❌ を修正：P2-1/P2-3✅、P4-3/P4-4✅、P5-2✅/P5-3🟡、P6-1/P6-2✅、反証層(B群)✅。
- 「実装の出来事の正＝docs/progress と architecture.html §0」と明記（spec は設計の正、進捗はそちら）。

## 受入
- doc のみ。全スイート green（テスト不変）。

## 自走完走の区切り（正直な未完）
- **XBRL自動引き当て**＝EDINET v2 が日付ベースAPIで company別の最新有報docID探索が非現実的（年単位の走査）。
  検出器(XBRL本文)は実装済だが自動引当は保留。
- **発注フック(record_order_entries)**＝手動約定＋通貨基準（¥ vs ネイティブ）の設計判断が要るため保留。record_entry は存在。
- これら2つは「自走で踏み込まない」と判断（推測でない設計判断・外部API制約）。

## architecture.html
- §0：MELCHIOR accrual反証行のハッシュ確定＋「spec同期」行。

## 状態
- 自走できる範囲は完走。残りは要ユーザー（launchd登録・初回live・moomoo同意②・EDINETキーは設定済）と
  要設計判断（発注フックの通貨基準・XBRL引当・Track Record UI=D-19・A/B育成=データ後）。
