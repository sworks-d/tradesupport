"""Exposure Coach — Market Posture 統合（X-2C minimal）.

Inspired by tradermonty/claude-trading-skills/exposure-coach (MIT).
Concept-only borrowing per D-21.

朝バッチの最前段ゲート：複数の市場指標から「今日新規エントリーを許容するか」を
1つのラベルに集約する。**決定論的計算**：LLMは触らない。

入力（すべて任意・欠落時は LOW confidence でフォールバック）：
- breadth_score: 市場の breadth（0-100、上昇銘柄比率等）
- uptrend_score: 上昇トレンド参加度（0-100）
- top_risk_score: 天井リスク（0-100、高い=天井近い）
- regime_score: マクロ regime の落ち着き（0-100）
- portfolio_dd_pct: PF 全体の DD（負の数）。D-23 -15% で強制 CASH_PRIORITY

出力（SCORE: NONE 適合・状態ラベル）：
- recommendation: NEW_ENTRY_ALLOWED / REDUCE_ONLY / CASH_PRIORITY
- bias: GROWTH / VALUE / NEUTRAL
- participation: BROAD / NARROW / UNKNOWN
- confidence: HIGH / MEDIUM / LOW（入力完全性に応じる）
- ceiling_pct: 推奨ネットエクスポージャー上限（コード由来）
- rationale: 1文の説明（テキスト）

D-23「DD-15%で新規停止」を強制ゲートとして埋め込む（外骨格の core）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Recommendation = Literal["NEW_ENTRY_ALLOWED", "REDUCE_ONLY", "CASH_PRIORITY"]
Bias = Literal["GROWTH", "VALUE", "NEUTRAL"]
Participation = Literal["BROAD", "NARROW", "UNKNOWN"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class ExposureInputs:
    """すべて任意・None は欠落として扱う。"""

    breadth_score: float | None = None
    uptrend_score: float | None = None
    top_risk_score: float | None = None
    regime_score: float | None = None
    institutional_score: float | None = None
    portfolio_dd_pct: float | None = None  # 負の数（例：-0.10 で -10%）


@dataclass
class ExposureDecision:
    """exposure_coach の出力（SCORE:NONE 適合）。"""

    recommendation: Recommendation
    bias: Bias
    participation: Participation
    confidence: Confidence
    ceiling_pct: int  # 0-100 推奨ネット曝露上限
    rationale: str
    component_scores: dict[str, float | None] = field(default_factory=dict)
    inputs_provided: list[str] = field(default_factory=list)
    inputs_missing: list[str] = field(default_factory=list)


# v2.4 TASK-EX1: 重み付き和の係数。環境変数で上書き可能（実測後の校正用）。
# 合計=1.0。top_risk は反転して使う（高い=曝露下げ要因）。
# 根拠（暫定）:
#   - breadth (0.30): 市場の幅は最重要 signal
#   - uptrend (0.25): トレンド方向の確度
#   - top_risk (0.20): 天井リスクは保守側
#   - regime (0.15): マクロ regime 落ち着き
#   - institutional (0.10): 機関フロー（弱め）
import os as _os_ex
def _w(name: str, default: float) -> float:
    return float(_os_ex.environ.get(f"EXPOSURE_W_{name.upper()}", str(default)))


_WEIGHTS = {
    "breadth_score": _w("breadth_score", 0.30),
    "uptrend_score": _w("uptrend_score", 0.25),
    "top_risk_score": _w("top_risk_score", 0.20),  # 反転
    "regime_score": _w("regime_score", 0.15),
    "institutional_score": _w("institutional_score", 0.10),
}

# D-23：DD-15% で強制 CASH_PRIORITY
_DD_HARD_GATE = -0.15

# v2.4 TASK-EX2: ceiling 閾値（recommendation 切替の境界）。名前付き定数化。
# 根拠: 65/35 は職人芸の暫定値。実証データで校正予定（実 PnL と posture の相関）。
# 65 = 「ceiling の上位 1/3」/ 35 = 「下位 1/3」（中間 1/3 が REDUCE_ONLY）
_CEILING_NEW_ENTRY_THRESHOLD = 65  # これ以上で新規エントリー許可
_CEILING_REDUCE_ONLY_THRESHOLD = 35  # これ以下は CASH_PRIORITY（35 以上 65 未満は REDUCE_ONLY）
# Participation 判定の境界
_PARTICIPATION_BROAD_THRESHOLD = 60
# Bias 判定の境界
_BIAS_GROWTH_THRESHOLD = 60
_BIAS_VALUE_THRESHOLD = 40


def _classify_confidence(provided: int, total: int) -> Confidence:
    """入力の完全性に応じて。3-5/5=HIGH、1-2/5=MEDIUM、0/5=LOW。"""
    if provided >= 3:
        return "HIGH"
    if provided >= 1:
        return "MEDIUM"
    return "LOW"


def _classify_participation(breadth: float | None, uptrend: float | None) -> Participation:
    """breadth + uptrend から市場参加幅を分類。"""
    if breadth is None and uptrend is None:
        return "UNKNOWN"
    # 両方ある場合の平均、片方だけならその値
    values = [v for v in (breadth, uptrend) if v is not None]
    avg = sum(values) / len(values)
    if avg >= _PARTICIPATION_BROAD_THRESHOLD:
        return "BROAD"
    return "NARROW"


def _classify_bias(regime: float | None, institutional: float | None) -> Bias:
    """暫定：regime / institutional から成長/価値の傾向を分類。"""
    if regime is None and institutional is None:
        return "NEUTRAL"
    values = [v for v in (regime, institutional) if v is not None]
    avg = sum(values) / len(values)
    if avg >= _BIAS_GROWTH_THRESHOLD:
        return "GROWTH"
    if avg <= _BIAS_VALUE_THRESHOLD:
        return "VALUE"
    return "NEUTRAL"


def _compute_ceiling(inputs: ExposureInputs) -> tuple[int, list[str], list[str]]:
    """重み付き和で ceiling_pct を算出。欠落入力は0%扱い（保守的）。"""
    total = 0.0
    weight_used = 0.0
    provided: list[str] = []
    missing: list[str] = []

    fields = {
        "breadth_score": inputs.breadth_score,
        "uptrend_score": inputs.uptrend_score,
        "top_risk_score": inputs.top_risk_score,
        "regime_score": inputs.regime_score,
        "institutional_score": inputs.institutional_score,
    }
    for name, value in fields.items():
        weight = _WEIGHTS[name]
        if value is None:
            missing.append(name)
            continue
        provided.append(name)
        # top_risk は反転して使う（高い=リスク高=曝露下げ）
        contribution = (100.0 - value) if name == "top_risk_score" else value
        total += weight * contribution
        weight_used += weight

    if weight_used == 0:
        # v2.5 TASK-EX4: 全入力 None で ceiling=0 を返すが、これは「入力不足」を意味する。
        # 0% を「市場が極端に弱い」と誤認しないよう missing 列に明示
        return 0, provided, missing
    # 重み正規化（欠落分は補完しない＝保守的）
    ceiling = int(total)
    return max(0, min(100, ceiling)), provided, missing


def decide_exposure(inputs: ExposureInputs) -> ExposureDecision:
    """全入力から posture を1つに集約。決定論的。"""
    ceiling, provided, missing = _compute_ceiling(inputs)
    confidence = _classify_confidence(len(provided), len(_WEIGHTS))
    participation = _classify_participation(inputs.breadth_score, inputs.uptrend_score)
    bias = _classify_bias(inputs.regime_score, inputs.institutional_score)

    # D-23 DD ハードゲート（最優先）
    dd_gate_active = (
        inputs.portfolio_dd_pct is not None and inputs.portfolio_dd_pct <= _DD_HARD_GATE
    )

    if dd_gate_active:
        recommendation: Recommendation = "CASH_PRIORITY"
        rationale = (
            f"PF DD={inputs.portfolio_dd_pct * 100:.1f}% (D-23 -15%ゲート発火)・新規停止"
        )
    elif confidence == "LOW":
        # 入力欠落時は保守的（読みが効かないと攻めない）
        recommendation = "REDUCE_ONLY"
        rationale = "入力不足（環境指標が揃わず）・既存ポジション以上は控える"
    elif ceiling >= _CEILING_NEW_ENTRY_THRESHOLD and participation != "NARROW":
        recommendation = "NEW_ENTRY_ALLOWED"
        rationale = (
            f"ceiling={ceiling}%・participation={participation}・新規エントリー可"
        )
    elif ceiling >= _CEILING_REDUCE_ONLY_THRESHOLD:
        recommendation = "REDUCE_ONLY"
        rationale = (
            f"ceiling={ceiling}%・新規は控え、既存からは利確優先"
        )
    else:
        recommendation = "CASH_PRIORITY"
        rationale = f"ceiling={ceiling}%・現金優先"

    return ExposureDecision(
        recommendation=recommendation,
        bias=bias,
        participation=participation,
        confidence=confidence,
        ceiling_pct=ceiling,
        rationale=rationale,
        component_scores={
            "breadth_score": inputs.breadth_score,
            "uptrend_score": inputs.uptrend_score,
            "top_risk_score": inputs.top_risk_score,
            "regime_score": inputs.regime_score,
            "institutional_score": inputs.institutional_score,
            "portfolio_dd_pct": inputs.portfolio_dd_pct,
        },
        inputs_provided=provided,
        inputs_missing=missing,
    )


__all__ = [
    "Bias",
    "Confidence",
    "ExposureDecision",
    "ExposureInputs",
    "Participation",
    "Recommendation",
    "decide_exposure",
]
