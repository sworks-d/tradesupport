---
name: system-overview
description: INVESTIGELION システム全体の構造・命名・データフローを再確認する。新規セッションや迷った時の道標。
---

# システム概観スキル

INVESTIGELION の構造を素早く再確認するためのスキル。

## このスキルが回答するもの

1. システムの目的・大指針
2. 命名と役割（WILLE/KATSURAGI/MAGI/ZEELE/DS 4 機）
3. 朝バッチ 18 ノード DAG の流れ
4. broker_mode × broker_provider × automation_mode の直交軸
5. 試験運用と楽天本番の並行運用構造
6. 11 ハルシネーション防壁
7. 段階的実弾移行（tier 設計）
8. 関連ドキュメント・ファイル一覧

## 実行手順

ユーザーが `/system-overview` と入力したら：

### 1. プロジェクトルートの CLAUDE.md を読む
```
[CLAUDE.md](/Users/shotaro/tradesupport/CLAUDE.md)
```
→ 大指針・現状・利用可能 Skills が記載されている。

### 2. docs/SYSTEM_PURPOSE.md を読む
```
[docs/SYSTEM_PURPOSE.md](/Users/shotaro/tradesupport/docs/SYSTEM_PURPOSE.md)
```
→ システム目的・運用指針・設計原則の正本。

### 3. メモリの主要項目を確認
```bash
ls ~/.claude/projects/-Users-shotaro-tradesupport/memory/
```

主要メモリ：
- `project_system_purpose.md` — システム最終目的
- `feedback_*.md` — 開発・運用の必須ルール
- `jp_ticker_suffix.md` — 技術的な落とし穴

### 4. 現状設定確認

```bash
.venv/bin/python -c "
from trading_agent.utils.lot_size import get_broker_mode, get_broker_provider, get_automation_mode, is_moomoo_live, get_live_tier
print('=== 現状設定 ===')
print(f'broker_mode: {get_broker_mode()}')
print(f'broker_provider: {get_broker_provider()}')
print(f'automation_mode: {get_automation_mode()}')
print(f'live_tier: {get_live_tier()}')
print(f'is_moomoo_live: {is_moomoo_live()}')
"
```

## 出力フォーマット

以下の構造で簡潔に提示：

```
🎯 システム概観

【目的】
INVESTIGELION = 個人向け投資自動売買システム
¥10 万から段階的に大規模化（少額時もスケール時も両立する設計）
成長銘柄を追うロジック（中小型）
試験運用 (paper) と楽天本番 (live) の並行運用

【命名と役割】
WILLE     : ユーザーの投資哲学
KATSURAGI : Treasury・安全装置
AKAGI     : 候補生成 (screening / ZEELE / market_analyst)
MAGI      : 3 審判 (MELCHIOR/BALTHASAR/CASPER) + Verification
ZEELE     : 戦略プール (7 preset)
DS 4 機   : REI / ASUKA / KAWORU / SHINJI (並行検証)
MISATO    : DS の司令塔・予算配分

【朝バッチ DAG】
pre_check → anomaly_check → topics_collector / universe_refresh
→ screening → zeele_curator / market_analyst → sell_recommender
→ portfolio_builder → trailing_check → close_due → pyramid_check
→ auto_fill → materialize_decisions → magi_verify → link_topics
→ summary → notify

【直交軸】
broker_mode: paper / live
broker_provider: moomoo / sbi / rakuten / monex / kabucom / fractional
automation_mode: manual / auto
live_tier: tier_1 (¥10k) → tier_5 (full)

【現状】
broker_mode={...} broker_provider={...} automation_mode={...}
paper Treasury: ¥X / live Treasury: ¥X

【利用可能 Skills】
/purchase-report  - 楽天購入報告 → DB 反映
/morning-status   - 朝の現状確認
/halt-recovery    - HALT 復旧
/system-overview  - このスキル

【関連ドキュメント】
docs/SYSTEM_PURPOSE.md           - システム目的・指針
docs/PURCHASE_REPORTING.md       - 購入報告フォーマット
docs/HALT_RECOVERY.md            - HALT 復旧手順
docs/OPERATIONS_SCHEDULING.md    - 朝バッチ運用
CLAUDE.md                         - プロジェクト全体ガイド（自動読込）
```

## このスキルが必要な場面

- 新規セッション開始時、文脈をすばやく取り戻したい
- 久しぶりに触る時、構造を再確認したい
- 大規模な変更を始める前、影響範囲を把握したい
- メモリ読み込みを補完したい

## メモリとの関係

- [[project_system_purpose]] — 大指針の正本
- 全 feedback_*.md — 開発・運用ルールの正本
- ここで概要、詳細は docs/ や対応する Skill を参照
