---
name: morning-status
description: 朝バッチ実行後の現状確認スキル。推奨銘柄、Treasury 残、HALT 状態、未完了 Decision、Portfolio 状況を一気に確認。
---

# 朝の状況確認スキル

朝バッチ実行後、または運用開始前に「今の状態」を一気に把握するためのスキル。

## このスキルが回答するもの

1. 今日の発注リスト（推奨 N 銘柄、優先度、合計予想額）
2. Treasury 残高（paper / live 別）
3. HALT 状態（発火してないか）
4. 未完了 awaiting Decision の件数
5. 現在の active Portfolio（broker_mode 別）
6. 直近朝バッチの実行ステータス（success / errors）
7. 設定確認（broker_mode / broker_provider / automation_mode）

## 実行手順

ユーザーが `/morning-status` と入力したら、以下を一気に実行して結果を整形して提示：

### 1. 設定確認
```bash
.venv/bin/python -c "
from trading_agent.utils.lot_size import get_broker_mode, get_broker_provider, get_automation_mode, is_moomoo_live
print(f'broker_mode: {get_broker_mode()}')
print(f'broker_provider: {get_broker_provider()}')
print(f'automation_mode: {get_automation_mode()}')
print(f'is_moomoo_live: {is_moomoo_live()}')
"
```

### 2. HALT 状態
```bash
.venv/bin/python -c "
from trading_agent.portfolio.anomaly_detector import get_halt_state
import json
print(json.dumps(get_halt_state(), ensure_ascii=False, indent=2, default=str))
"
```

### 3. Treasury 残高
```bash
.venv/bin/python -c "
from pathlib import Path
from trading_agent.db import get_engine
from trading_agent.portfolio.misato import treasury_view
engine = get_engine(Path('data/trading.sqlite'))
import json
print('paper:', json.dumps(treasury_view(engine, 'paper'), ensure_ascii=False, default=str))
print('live :', json.dumps(treasury_view(engine, 'live'), ensure_ascii=False, default=str))
"
```

### 4. 直近朝バッチ状態
```bash
.venv/bin/python -c "
from pathlib import Path
from sqlmodel import Session
from trading_agent.db import get_engine
from trading_agent.models.batch import BatchState
from trading_agent.utils.time_utils import today_jst
engine = get_engine(Path('data/trading.sqlite'))
inv = f'morning_{today_jst().isoformat()}'
with Session(engine) as s:
    bs = s.get(BatchState, inv)
    if bs:
        print(f'invocation_id: {inv}')
        print(f'status: {bs.status}')
        print(f'duration: {(bs.ended_at - bs.started_at).total_seconds()/60:.1f} 分')
        ns = bs.node_status or {}
        success = sum(1 for v in ns.values() if v == 'success')
        print(f'ノード success: {success}/{len(ns)}')
        print(f'errors: {bs.errors or \"(なし)\"}')
    else:
        print(f'今日の朝バッチ ({inv}) 未実行')
"
```

### 5. 推奨銘柄（awaiting Decision）
```bash
.venv/bin/python -c "
from pathlib import Path
from sqlmodel import Session, select, col
from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.utils.time_utils import today_jst
engine = get_engine(Path('data/trading.sqlite'))
today = today_jst()
with Session(engine) as s:
    decs = list(s.exec(
        select(Decision)
        .where(col(Decision.date) == today)
        .where(col(Decision.status) == 'awaiting')
        .where(col(Decision.action) == 'buy')
        .order_by(col(Decision.ticker))
    ).all())
    print(f'今日の決裁待ち buy Decision: {len(decs)} 件')
    for d in decs:
        u = s.get(Universe, d.ticker)
        name = (u.name if u else '?')[:20]
        cap = f'¥{int((u.market_cap_jpy or 0)/1e9)}B' if u and u.market_cap_jpy else '?'
        print(f'  {d.ticker}  {name:<20s}  cap={cap}  stance={d.gendo_stance}')
"
```

### 6. Portfolio 状況
```bash
.venv/bin/python -c "
from pathlib import Path
from sqlmodel import Session, select, col
from trading_agent.db import get_engine
from trading_agent.models.portfolio import Portfolio
engine = get_engine(Path('data/trading.sqlite'))
with Session(engine) as s:
    for mode in ('paper', 'live'):
        ports = list(s.exec(
            select(Portfolio)
            .where(col(Portfolio.status) == 'active')
            .where(col(Portfolio.broker_mode) == mode)
        ).all())
        print(f'{mode}: active {len(ports)} 件')
        for p in ports[:5]:
            pnl = ''
            print(f'  {p.ticker}  ¥{int(p.buy_price or 0):,} × {p.qty} 株  personality={p.personality or \"-\"}')
"
```

### 7. 発注リスト HTML 確認
```bash
ls -la autoreport/orders/$(date -j +%Y-%m-%d).html 2>&1
```

## 出力フォーマット

以下のように整形して返す：

```
🌅 朝の状況サマリ（2026-XX-XX）

⚙️ 設定
  broker_mode: paper (試験運用)
  broker_provider: rakuten (楽天かぶミニ)
  automation_mode: manual

🛑 HALT
  発火中: No

💰 Treasury
  paper (試験運用): ¥100,000
  live  (楽天本番): ¥100,000

🔄 直近朝バッチ
  status: success
  duration: 18.5 分
  ノード: 18/18 success

📋 今日の決裁待ち 13 件
  3697  SHIFT                cap=¥179B  要検討
  3994  Money Forward        cap=¥240B  静観
  ...

📦 active Portfolio
  paper: 0 件
  live:  0 件

📄 発注リスト
  autoreport/orders/2026-XX-XX.html (NN KB)

✅ 次のアクション:
  - 楽天証券で発注 → /purchase-report で報告
  - 何か異常があれば指摘してください
```

## 状態によって異なる注意点

- HALT 発火中なら **赤字で警告**、`/halt-recovery` を案内
- 朝バッチが errors=null でない場合、原因調査を提案
- Treasury 残が想定額と乖離してたら指摘
- 推奨銘柄ゼロなら「市場休日？」「閾値高すぎ？」を確認
