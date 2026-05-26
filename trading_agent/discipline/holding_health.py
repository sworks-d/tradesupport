"""Holding Health — Kanchi T1-T5 翻案（X-2B minimal）.

Inspired by tradermonty/claude-trading-skills/kanchi-dividend-review-monitor (MIT).
Concept-only borrowing per D-21.

保有銘柄ごとに**強制見直しトリガー T1-T5** を走らせ、状態を `OK / WARN / REVIEW` に分類。
**絶対に auto-sell しない**。REVIEW は人間判断の入口。

T1-T5（D-24 X2 spec §2.2 / claude-trading-skills の Kanchi スキーマを JP 文脈に翻案）:
  - **T1**: 配当減配・無配（日次）→ REVIEW
  - **T2**: カバレッジ悪化（四半期）→ REVIEW
  - **T3**: 信用代理指標悪化（週次）→ WARN
  - **T4**: 適時開示キーワード（日次）→ REVIEW
  - **T5**: 構造的悪化（四半期）→ WARN

MVP の方針：
- T1: 配当データがあれば判定、無ければスキップ
- T2/T5: 四半期データが必要 → 暫定スキップ（要 EDINET 連携で X-2B 完成）
- T3: 直近の価格パフォーマンス vs ベンチマーク（暫定ルール）
- T4: topics テーブルにこの ticker 関連のリスクキーワードがあれば発火

すべてコード由来。LLM は使わない（D-23 整合）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

State = Literal["OK", "WARN", "REVIEW"]

# 状態の優先順（REVIEW > WARN > OK）
_STATE_PRIORITY: dict[State, int] = {"OK": 0, "WARN": 1, "REVIEW": 2}


@dataclass
class TriggerEvidence:
    """1つのトリガーが発火した根拠。"""

    trigger_id: str  # "T1" 〜 "T5"
    state: State
    reason: str
    metric: dict[str, Any] = field(default_factory=dict)


@dataclass
class HoldingFinding:
    """1銘柄の health 評価結果。"""

    ticker: str
    state: State
    triggers_fired: list[str]  # ["T1", "T4"] 等
    evidence: list[TriggerEvidence]
    next_review_date: str | None = None  # ISO date
    manual_check_required: bool = False  # state == REVIEW なら True

    @property
    def has_triggers(self) -> bool:
        return len(self.triggers_fired) > 0


# === 適時開示・ニュースの REVIEW 級リスクキーワード ===
# claude-trading-skills の英語キーワードを JP 文脈に翻案
T4_REVIEW_KEYWORDS: tuple[str, ...] = (
    "不適切会計",
    "粉飾",
    "会計訂正",
    "決算修正",
    "監査意見",
    "GC注記",
    "ゴーイング・コンサーン",
    "上場廃止",
    "特設注意",
    "監理銘柄",
    "業務改善命令",
    "課徴金",
    "重大事故",
    "不祥事",
    "リコール",
    "訴訟",
    "民事再生",
    "破産",
    "going concern",
    "restatement",
    "fraud",
    "investigation",
    "delisting",
)
T4_WARN_KEYWORDS: tuple[str, ...] = (
    "業績下方修正",
    "減益",
    "減配",
    "無配",
    "減損",
    "未達",
    "guidance cut",
    "downgrade",
    "dividend cut",
    "impairment",
)


def _check_t1_dividend_cut(
    latest_regular: float | None, prior_regular: float | None
) -> TriggerEvidence | None:
    """T1: 配当減配・無配。latest < prior * 0.5 で REVIEW。"""
    if latest_regular is None or prior_regular is None:
        return None
    if prior_regular <= 0:
        return None
    if latest_regular == 0:
        return TriggerEvidence(
            trigger_id="T1",
            state="REVIEW",
            reason=f"配当ゼロ（前回 {prior_regular} → 今回 0）",
            metric={"latest": latest_regular, "prior": prior_regular},
        )
    if latest_regular < prior_regular * 0.5:
        cut_pct = (1 - latest_regular / prior_regular) * 100
        return TriggerEvidence(
            trigger_id="T1",
            state="REVIEW",
            reason=f"配当 -{cut_pct:.0f}%（{prior_regular} → {latest_regular}）",
            metric={"latest": latest_regular, "prior": prior_regular, "cut_pct": cut_pct},
        )
    return None


def _check_t3_credit_proxy(
    perf_pct: float | None, benchmark_perf_pct: float | None, volatility: float | None
) -> TriggerEvidence | None:
    """T3: 信用代理指標。ベンチ比 -20% かつ高ボラ。WARN。"""
    if perf_pct is None or benchmark_perf_pct is None:
        return None
    relative = perf_pct - benchmark_perf_pct
    if relative < -0.20:  # -20% 以下
        # ボラ高もチェック可能なら
        if volatility is not None and volatility > 0.40:
            return TriggerEvidence(
                trigger_id="T3",
                state="WARN",
                reason=f"ベンチ比 {relative * 100:.1f}% + 高ボラ（{volatility * 100:.1f}%）",
                metric={"relative_perf": relative, "volatility": volatility},
            )
        return TriggerEvidence(
            trigger_id="T3",
            state="WARN",
            reason=f"ベンチ比 {relative * 100:.1f}%（信用悪化の代理指標）",
            metric={"relative_perf": relative},
        )
    return None


def _check_t4_disclosure(filings_text: str | None) -> TriggerEvidence | None:
    """T4: 適時開示・ニュースのキーワード。REVIEW or WARN。"""
    if not filings_text:
        return None
    text = filings_text.lower()
    # REVIEW 級から先に判定
    for kw in T4_REVIEW_KEYWORDS:
        if kw.lower() in text:
            return TriggerEvidence(
                trigger_id="T4",
                state="REVIEW",
                reason=f"重要キーワード検出：{kw}",
                metric={"keyword": kw},
            )
    for kw in T4_WARN_KEYWORDS:
        if kw.lower() in text:
            return TriggerEvidence(
                trigger_id="T4",
                state="WARN",
                reason=f"注意キーワード検出：{kw}",
                metric={"keyword": kw},
            )
    return None


def _max_state(states: list[State]) -> State:
    """状態リストの最高 severity（REVIEW > WARN > OK）。"""
    if not states:
        return "OK"
    return max(states, key=lambda s: _STATE_PRIORITY[s])


def check_holding(holding: dict[str, Any]) -> HoldingFinding:
    """1銘柄を T1-T5 で評価。

    holding スキーマ（柔軟・欠落OK）:
        {
            "ticker": str,
            "dividend": {"latest_regular": float, "prior_regular": float},  # T1
            "perf_pct": float, "benchmark_perf_pct": float, "volatility": float,  # T3
            "filings_text": str,  # T4 用の最近の開示・ニュース連結テキスト
            # T2/T5 は四半期データが要るので MVP 範囲外
        }
    """
    ticker = holding.get("ticker", "")
    evidence: list[TriggerEvidence] = []

    # T1
    div = holding.get("dividend") or {}
    e1 = _check_t1_dividend_cut(div.get("latest_regular"), div.get("prior_regular"))
    if e1:
        evidence.append(e1)

    # T3
    e3 = _check_t3_credit_proxy(
        holding.get("perf_pct"),
        holding.get("benchmark_perf_pct"),
        holding.get("volatility"),
    )
    if e3:
        evidence.append(e3)

    # T4
    e4 = _check_t4_disclosure(holding.get("filings_text"))
    if e4:
        evidence.append(e4)

    state = _max_state([e.state for e in evidence])
    return HoldingFinding(
        ticker=ticker,
        state=state,
        triggers_fired=[e.trigger_id for e in evidence],
        evidence=evidence,
        manual_check_required=(state == "REVIEW"),
    )


@dataclass
class HealthReport:
    """全保有のヘルスチェック結果（X2 spec §2.2 の出力契約に整合）。"""

    summary: dict[State, int]
    findings: list[HoldingFinding]
    review_tickets: list[HoldingFinding]  # state==REVIEW のみ
    data_asof: str = ""


def check_all_holdings(holdings: list[dict[str, Any]], data_asof: str = "") -> HealthReport:
    """全保有を T1-T5 で評価。**絶対に auto-sell しない** — REVIEW は人間判断の入口。"""
    findings = [check_holding(h) for h in holdings]
    summary: dict[State, int] = {"OK": 0, "WARN": 0, "REVIEW": 0}
    for f in findings:
        summary[f.state] = summary.get(f.state, 0) + 1
    review_tickets = [f for f in findings if f.state == "REVIEW"]
    return HealthReport(
        summary=summary,
        findings=findings,
        review_tickets=review_tickets,
        data_asof=data_asof,
    )


__all__ = [
    "HealthReport",
    "HoldingFinding",
    "State",
    "T4_REVIEW_KEYWORDS",
    "T4_WARN_KEYWORDS",
    "TriggerEvidence",
    "check_all_holdings",
    "check_holding",
]
