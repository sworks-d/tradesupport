"""Discipline layer — the "exoskeleton" around MAGI.

Concrete implementation of D-23（規律層・外骨格）, specified in
docs/plan/spec/X2_claude_trading_skills_adoption.md.

Inspired by tradermonty/claude-trading-skills (MIT) — adopted as the
project's design north star in D-24 (2026-05-26). All modules here are
re-implementations of the ideas: no code direct-imported, only concepts
translated to tradesupport's "数値はコード／出典必須／SCORE:NONE" contract.

Modules (by X-2 phase):

- thesis_store (X-2A): investment thesis lifecycle store
  (IDEA → ENTRY_READY → ACTIVE → CLOSED)
  Persists to tradesupport's main SQLite DB via SQLModel `Thesis` table.
  ← inspired by claude-trading-skills/trader-memory-core

Planned (next):

- holding_health (X-2B): Kanchi T1-T5 forced-review trigger engine
  ← inspired by claude-trading-skills/kanchi-dividend-review-monitor
- exposure_coach (X-2C): Market posture synthesizer
  ← inspired by claude-trading-skills/exposure-coach
- postmortem (X-2D): closed-trade outcome classifier
  ← inspired by claude-trading-skills/signal-postmortem
- performance_coach (X-2D): process/risk/execution/behavior reviewer
  ← inspired by claude-trading-skills/trade-performance-coach
"""

from trading_agent.discipline import thesis_store

__all__ = ["thesis_store"]
