"""規律層（外骨格）の確定パラメータ。仕様：`docs/plan/spec/G_risk_discipline.md` G-0。

RISK_EXOSKELETON（2026-05-23 確定）の8数値を、¥100k・20%上限・現金20%下限・R-mult で
相互に閉じるよう整合させた採用値。**数値はここが単一の正**（コードはこれを参照する）。
規律は判断でない＝SCORE:NONE と無関係。全てコード（LLM非関与）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace


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
    # Core-Satellite（稼ぎ=所有×時間×複利）。コア=質分散の積立放任／サテライト=隔離した小さな賭け。
    core_fraction: float = 0.85  # コア：質分散塊を積立・勝ち放任・地雷除外
    satellite_fraction: float = 0.15  # サテライト：V字/小型/テーマを小さく・分散・損切り固定
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

    def core_budget_jpy(self, account_total_jpy: float) -> float:
        """コア（持ち続ける質分散塊）に回す額。"""
        return account_total_jpy * self.core_fraction

    def satellite_budget_jpy(self, account_total_jpy: float) -> float:
        """サテライト（小さく隔離した賭け）に回す額。"""
        return account_total_jpy * self.satellite_fraction


# 既定インスタンス（¥100k版）。口座サイズに応じた逓減は params_for_account() で得る。
DEFAULT_RISK = RiskParams()


@dataclass(frozen=True)
class TaperTier:
    """口座サイズ帯ごとの逓減設定。口座が育つほど risk% を下げ、銘柄数を増やす。"""

    min_account_jpy: float
    risk_per_trade: float
    max_positions: int
    cash_floor: float


# 逓減スケジュール（G-0 ⑥ 補完）。
# 思想：株のリターンは極端に偏る（Bessembinder）＝勝ちは少数の大化けに集中する。よって口座が
# 育つほど(1)1トレードrisk%を下げて致命傷を遠ざけ、(2)銘柄数を増やして"大化けを取りこぼさない"
# 網を広げる。¥100kは訓練・実弾検証の最小単位で5銘柄固定だが、成長に応じ分散を広げる。
# ※ tier 境界は min_account_jpy 以上で最大の帯を採用（昇順）。数値は採用値（要実績で更新）。
# フィールド順：(min_account_jpy, risk_per_trade, max_positions, cash_floor)
TAPER_SCHEDULE: tuple[TaperTier, ...] = (
    TaperTier(0.0, 0.020, 5, 0.20),
    TaperTier(300_000.0, 0.015, 8, 0.20),
    TaperTier(1_000_000.0, 0.012, 12, 0.15),
    TaperTier(3_000_000.0, 0.010, 16, 0.15),
    TaperTier(10_000_000.0, 0.008, 20, 0.10),
)


def params_for_account(account_total_jpy: float, base: RiskParams = DEFAULT_RISK) -> RiskParams:
    """口座サイズに応じて risk%・銘柄数・現金下限を逓減/拡張した RiskParams を返す。

    口座が大きいほど 1トレードrisk% を下げ（致命傷回避）、銘柄数を増やす（偏った分布で
    大化けを取りこぼさない網を広げる）。他のパラメータ（stop幅・上限・ゲート）は base を継承。
    """
    tier = TAPER_SCHEDULE[0]
    for t in TAPER_SCHEDULE:
        if account_total_jpy >= t.min_account_jpy:
            tier = t
    return replace(
        base,
        risk_per_trade=tier.risk_per_trade,
        max_positions=tier.max_positions,
        cash_floor=tier.cash_floor,
    )
