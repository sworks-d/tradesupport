# 0003 データ収集をarchitecture粒度で詳細化（pipeline.html）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「データ収集をもっと詳細にして。もしかしてこれぐらいしかないの？」
- 「architecture.html よりももっと細かくして。」

## 実施内容
- `architecture.html`（§3 外部リソース・§4 MCPツール）を読み、設計上のデータ層を把握。
  - 設計ソース：moomoo OpenAPI / SEC EDGAR / EDINET / TDnet RSS / NewsAPI / 各種RSS（9to5Mac/TechCrunch/日経/政府公式 FRB・財務省・ホワイトハウス）/ J-Quants / yfinance / Anthropic / Ollama。
  - MCPツール：market_data / fundamentals / disclosure / news / technicals / screening / broker_read(NEW) / llm_call。
- `docs/plan/pipeline.html` の「A. データ収集」を **A-1相場／A-2財務／A-3文脈／A-4口座／A-5横断** に細分化。
  各ノードに **設計ソース・現状ソース・状態・担当ツール・穴** を併記（architecture.html のフラット表より細かい粒度）。
- 回答：「これぐらいしかない」のではなく、**設計は広いが実装済みは A-1相場・テクニカル・財務サマリ・横断照合のみ**。
  文脈（ニュース/開示/マクロ）・財務一次情報・口座データが欠落、と明示。

## コミット
- 本md＋pipeline.html＋CSS追加を同一コミット。

## 状態/次
- 過不足はユーザー確認待ち。最短：①ニュース源（無料）でCASPER起動／②外部リポジトリ棚卸し。
