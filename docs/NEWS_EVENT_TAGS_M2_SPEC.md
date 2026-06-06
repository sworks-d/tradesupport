# M2: 構造化イベントタグ 仕様（凍結・発火データ待ち）

> 状態: **仕様のみ。実装は凍結。** A（news_positive/negative の record-only 接続）と M1（発火率可視化）が
> 着地済。M2 は M1 の **実発火データを見てから、効きそうな順に薄く投入**する（claude/codex 合意 2026-06-06）。
> B（売買スコアへの重み付け）はさらに後 ——「タグが付く」でなく「**タグ付き判断が勝っている**」が
> edge器(`compare_signal_tags_vs_baseline`, n≥20)で見えてから。

## 背景（なぜ凍結か）

- A の news タグは headline 辞書由来。実見出しの発火率は **TDnet 1.7% / EN 0%**（実測）。
- 構造化の既存タグ `earnings_accel` も verified 125件中 **3件=2.4%**（M1 実測）。
- → 「中期エントリー時点で、その銘柄に直近の材料イベントがある確率自体が低い」。どの方式でも
  per-decision 発火は数%で、edge器が n≥20 に達するのは月単位。**未観測のままタグを増やすのは盲目的**
  （[[feedback_pipeline_observability]]）。だから M1 で発火を見てから薄く入れる。

## データソース確認（推測でなく実フィールド・2026-06-06 dry-read）

J-Quants Free `get_fin_summary` の実列（[jquants.py:132](../trading_agent/mcp_tools/jquants.py#L132)）:
`DiscDate(開示日)`, `Code`, `CurPerEn`, `Sales`, `OP`, `NP`, `TA`, `CFO`, `EPS`, `BPS`, `Eq`,
`F*/Nx*(予想)`, `Div*(配当)`。
現状 [PeriodFinancials](../trading_agent/screening/financials.py#L288) が抽出しているのは
**Sales/OP/NP/TA/CFO/shares のみ**。**予想(F*/Nx*)・配当(Div*) は生 statement に在るが未パース**。

## 候補イベント × 実データソース × 導出可否

| イベント | タグ案 | 正本ソース（確認済） | 今すぐ導出可？ |
|---|---|---|---|
| 業績加速 | `earnings_accel`（既存） | J-Quants NP 二階微分（assess_turnaround） | ✅ 実装済 |
| 上方修正 | `event_upward_revision` | J-Quants 予想 F*/Nx* の逐次改定（前回予想→今回予想↑） | △ **パーサ拡張が前提**（F*/Nx* 未抽出）|
| 下方修正 | `event_downward_revision` | 同上（予想↓） | △ パーサ拡張が前提 |
| 決算サプライズ/PEAD | `event_earnings_surprise` | J-Quants 実績(NP/Sales) vs 予想(F*) を DiscDate 基準で | △ パーサ拡張が前提 |
| 増配 | `event_dividend_hike` | J-Quants Div* の前期比↑ | △ パーサ拡張が前提 |
| 減配 | `event_dividend_cut` | 同上（↓） | △ パーサ拡張が前提 |
| 自社株買い | `event_buyback` | **statements に無し → TDnet 開示見出しのみ** | ✗ headline 依存（低発火）|
| 希薄化/増資 | `event_dilution` | shares 変化 or TDnet 開示見出し | ✗/△（shares は逆算値で精度低）|
| 月次売上好調 | `event_monthly_strong` | **四半期 statements に無し → 月次開示のみ** | ✗ headline 依存 |
| 大型提携/受注 | `event_partnership` / `event_large_order` | **statements に無し → TDnet 開示見出しのみ** | ✗ headline 依存 |

### 含意（投入順の根拠）

1. **構造化レバーの本命は「予想(F*/Nx*)・配当(Div*) のパーサ拡張」**。これで
   上方修正/下方修正/サプライズ/増配減配 が一気に構造化導出可能になる（headline 辞書の壁を回避）。
2. **自社株買い/提携/受注/月次は J-Quants に無く headline 依存** → A の 1.7% 問題から逃れられない。
   ここは LLM 分類 or 専用 TDnet パーサが要るが、費用対効果は M1 の発火実測を見てから判断。

## 実装規約（A と同じレールに乗せる）

- タグ命名: `event_<snake>`。`_signal_tag_firing` は `event_` prefix を構造化発火として既に集計する。
- 導出関数: `derive_earnings_signal_tags` と同じ `(tags, evidence)` 契約で
  `derive_structured_event_tags(fin, *, asof)` を `screening/turnaround.py` 付近に追加。
  evidence に DiscDate / 前回予想 / 今回予想 / 値 を残す（監査・PIT）。
- 配線: 既存 `earnings_sink` と同じ経路（`_apply_record_only_tags`・record-only・売買不変・冪等）。
  新規 sink を増やさず earnings_sink に合流させてよい（タグ名で区別できる）。
- **PIT 厳守**: イベント日 = DiscDate。verify=fill 前に刻む。過去 decision への backfill はしない。
- **record-only**: B 重み付けは別タスク。n≥20 かつ edge器で正味エッジ確認後のみ。

## 投入手順（M1 実測ドリブン）

1. M1 を実バッチで回し（ユーザー判断）、`signal_tag_firing` で **news/structured の実発火率**を観測。
2. パーサ拡張（F*/Nx*/Div*）→ 上方修正/増配/サプライズの3つを**薄く**投入（最も中期に効く・PEAD）。
3. 再度 M1 で発火率を確認。発火が n≥20 に向かうものだけ残し、伸びないものは落とす。
4. headline 依存イベント（自社株買い等）は、構造化が出揃って発火が足りない時のみ着手判断。
5. B 重み付けは、edge器で「タグ付き判断が勝っている」が出てから初めて検討。

## やらないこと（明示）

- ❌ 発火実測前に全候補を一括実装（盲目的・複雑化）
- ❌ headline 辞書の深掘り（天井が低い・補助に格下げ済）
- ❌ EN headline 対応（ユニバースは JP 中小型・優先度低）
- ❌ B 重み付けの先行（タグ存在≠勝ち寄与）
