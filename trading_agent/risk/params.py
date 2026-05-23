"""規律層（外骨格）の確定パラメータ。仕様：`docs/plan/spec/G_risk_discipline.md` G-0。

RISK_EXOSKELETON（2026-05-23 確定）の8数値を、¥100k・20%上限・現金20%下限・R-mult で
相互に閉じるよう整合させた採用値。**数値はここが単一の正**（コードはこれを参照する）。
規律は判断でない＝SCORE:NONE と無関係。全てコード（LLM非関与）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskParams:
    # ① 1トレードのリスク（口座残高比）。口座増で逓減させる前提（今は2%）。
    risk_per_trade: float = 0.02
    # ② 同時保有上限（投資可能¥80k ÷ R-mult¥13–20k ＝ 実質4–6 → 5）
    max_positions: int = 5
    # ③ 1テーマ/セクター上限（銘柄数 と 口座比）。AI集中=1ベットを止める
    max_per_theme: int = 2
    max_theme_weight: float = 0.30
    # ④ 現金比率の下限（常時現金）
    cash_floor: float = 0.20
    # ⑤ 高値からのDDで新規エントリー停止（クールダウン）
    drawdown_halt: float = 0.15
    # ⑦ 損切り幅（既定）。中期=日次ノイズで切らない。10–15%、既定12%
    default_stop_pct: float = 0.12
    stop_pct_min: float = 0.10
    stop_pct_max: float = 0.15
    # 1銘柄サイズ上限（総資産比）。stop≥10%ならR-multは常にこれ以下
    max_position_weight: float = 0.20
    # ⑥ 増額ゲート（¥100k自体は実弾。これを満たすまで増額しない）
    gate_min_decisions: int = 30
    gate_max_drawdown: float = 0.15  # 期間中の最大DDがこれ以内
    gate_min_avg_r: float = 0.0  # 平均R>0

    def investable_fraction(self) -> float:
        """投資に回せる比率（現金下限を除く）。"""
        return 1.0 - self.cash_floor

    def one_r_jpy(self, account_total_jpy: float) -> float:
        """1R（許容損失額）。anti-martingale＝口座残高に対する固定%（残高が減れば縮む）。"""
        return account_total_jpy * self.risk_per_trade


# 既定インスタンス（¥100k版）。将来は settings から差し替え可能にする。
DEFAULT_RISK = RiskParams()
