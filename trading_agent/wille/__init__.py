"""WILLE — 投資判断システムの包括組織（旧 MISATO 階層を再構成）。

組織図:
  WILLE（包括組織）
    ├─ Treasury（預かり金管理）+ 安全装置（HALT・予算上限）
    ├─ 🎖 MISATO（実働・作戦指示）  — wille.misato
    ├─ 🧪 RITSUKO（実働・調査分析） — wille.ritsuko
    └─ DS 4 機（執行者・規律）       — REI / ASUKA / SHINJI / KAWORU

WILLE の責務（組織レベル）:
  - 預かり金管理（seed_jpy）
  - DS 4 機への予算配分（PilotAllocation）
  - 全体安全装置（HALT / 上限クランプ / dry-run→approve）
  - paper_fill 執行のオーケストレーション

MISATO の責務（作戦指示）:
  - RITSUKO の分析 + WILLE の予算 を統合
  - 銘柄ごとに「どの DS にどう振るか」の作戦指示
  - 状況に応じた攻め / 守り / 静観の判断

RITSUKO の責務（調査分析）:
  - 銘柄状況判定（強気/弱気/押し目/中立）
  - 市場 regime（地合い）
  - MAGI/ZEELE 統合分析
"""

from trading_agent.wille import misato, ritsuko

__all__ = ["misato", "ritsuko"]
