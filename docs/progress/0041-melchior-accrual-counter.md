# 0041 MELCHIOR accrual反証（利益の質の赤を2期財務から摘出）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「自走部分は完走して」。研究 領域2-B（earnings quality）の MELCHIOR反証。

## 実施内容
- `screening/financials.py`：`inventory` を追加（field map＋PeriodFinancials・在庫反証用）。
- `screening/credibility.py::melchior_accrual_counter(fin)`：2期財務から**利益の質の赤をコード摘出**：
  ① 営業CF<純利益×0.8（現金裏付け弱）② 発生高(NI−CFO)/総資産>0.10（Sloan系）
  ③ DSO悪化（売掛/売上 が前年比+20%超＝計上前倒しの疑い）④ 在庫が売上より+10%超速く増加（滞留）。
  全てコード（R1）・データ摘出（R5）・欠損は出さない（R4）・出典付き。
- `magi/persist._apply_credibility`：信用性ゾーン由来＋accrual由来の反証を MELCHIOR に併記。
- `screening/__init__` で公開。テスト `test_credibility.py` +4（CFO<NI＋発生高／DSO悪化／在庫増／健全は空）。
- 併せて §1 の標準入れ忘れ lint（standin.py コメント長）を解消。

## 受入
- 全スイート green。trading_agent ruff clean（残2件は pre-existing の test/scripts）。

## architecture.html
- §0：バックテスト行のハッシュ確定＋「MELCHIOR accrual反証」行。

## 状態/次（自走の続き）
- 残り：発注フック record_order_entries（P5-3）／XBRL自動引き当て／spec同期。
