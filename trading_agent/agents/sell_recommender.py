"""sell-recommender エージェント（AGENT_SPECS.md §3 / PANEL_SPECS C-1.2）。

保有銘柄を毎朝チェックし、利確 or 損切りを推奨する。

- 利確スコア＝目標達成度40% + シナリオ達成度30% + テクニカル悪化20% + AI確信度10%
- 損切りスコア＝シナリオ崩壊40% + 損失幅30% + ネガティブニュース20% + AI確信度10%
  （重みは CLAUDE_CODE_INSTRUCTIONS §8.1 / PANEL_SPECS の【たたき台】）
- シナリオ進捗（thesis_checklist 評価）は LLM。未登録/失敗時は health=0.5 で縮退。
- 損切り score>=70 では「規律メッセージ」を必ず付与（§3.6、FX 経験を踏まえた設計）。
- ネガティブニュース軸は Phase 1 では 0（LLM センチメントは後日）。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.agents.serialization import save_scenarios, save_sell_signals
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.mcp_tools.market_data import MarketDataInput
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import Scenario, SellSignal
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_SELL_THRESHOLD = 50
_DISCIPLINE_THRESHOLD = 70
_RECENT_DAYS = 3


class SellRecommenderInput(AgentInput):
    tickers: list[str] | None = None
    skip_recently_bought: bool = True


class SellRecommenderOutput(AgentOutput):
    sell_signals: list[dict[str, Any]] = Field(default_factory=list)
    scenario_updates: list[dict[str, Any]] = Field(default_factory=list)


# ---- スコア成分（純粋関数。0-1 を返す） ------------------------------------


def target_achievement(current: float, buy: float, target_pct: float) -> float:
    if buy <= 0 or target_pct <= 0:
        return 0.0
    gain = (current - buy) / buy
    return max(0.0, min(gain / target_pct, 1.0))


def technical_warning(rsi: float | None) -> float:
    if rsi is None:
        return 0.0
    if rsi >= 70:
        return 1.0
    if rsi >= 60:
        return (rsi - 60) / 10
    return 0.0


def loss_magnitude(current: float, buy: float, stop_loss_pct: float) -> float:
    if buy <= 0 or stop_loss_pct >= 0:
        return 0.0
    loss = (current - buy) / buy
    if loss >= 0:
        return 0.0
    return max(0.0, min(loss / stop_loss_pct, 1.0))  # stop ライン到達で 1.0


def profit_taking_score(
    target_ach: float, scenario_ach: float, tech_warning: float, ai_confidence: float
) -> float:
    return (target_ach * 0.4 + scenario_ach * 0.3 + tech_warning * 0.2 + ai_confidence * 0.1) * 100


def stop_loss_score(
    scenario_break: float, loss_mag: float, negative_news: float, ai_confidence: float
) -> float:
    return (scenario_break * 0.4 + loss_mag * 0.3 + negative_news * 0.2 + ai_confidence * 0.1) * 100


def status_from_health(health: float) -> str:
    if health >= 0.7:
        return "intact"
    if health >= 0.4:
        return "weakening"
    return "broken"


def determine_sell_recommendation(
    signal_type: str, score: int, qty: int, current_price: float
) -> dict[str, Any]:
    """売却数量・指値（§3.7）。"""
    if signal_type == "profit_taking":
        if score >= 90:
            return {
                "type": "limit",
                "price": round(current_price * 0.997, 4),
                "qty": qty,
                "qty_label": "全量",
                "note": "利確",
            }
        return {
            "type": "limit",
            "price": round(current_price * 0.997, 4),
            "qty": qty // 2,
            "qty_label": "半量",
            "note": "利確",
        }
    return {"type": "market", "qty": qty, "qty_label": "全量（成行）", "note": "損切り（規律）"}


def discipline_reasons(
    buy_price: float, stop_loss_pct: float, current_price: float
) -> list[dict[str, Any]]:
    """損切り高スコア時の規律メッセージ（§3.6）。"""
    stop_price = buy_price * (1 + stop_loss_pct)
    return [
        {
            "title": "事前に決めた損切りラインに到達",
            "detail": (
                f"取得 {buy_price} / 損切りライン {stop_loss_pct * 100:.0f}%"
                f"（={stop_price:.1f}）。現在 {current_price}。ルールを破ると規律が崩壊する。"
            ),
            "priority": "規律",
        },
        {
            "title": "感情的な判断で傷を広げる典型パターン",
            "detail": "「もう少し待てば戻る」が損失拡大の最大要因。機械的に切るのが規律。",
            "priority": "規律",
        },
    ]


class SellRecommenderAgent(Agent[SellRecommenderInput]):
    """保有銘柄の利確 / 損切り推奨エージェント。"""

    name = "sell_recommender"
    description = "保有銘柄を評価し、利確 / 損切りシグナルとシナリオ進捗を出力する。"
    required_tools = ["market_data", "technicals", "fundamentals", "news", "llm_call"]
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: SellRecommenderInput) -> AgentOutput:
        holdings = self._load_holdings(self._ctx.engine, agent_input.tickers)
        today = utcnow().date()

        sell_signals: list[SellSignal] = []
        scenario_rows: list[Scenario] = []
        signal_views: list[dict[str, Any]] = []
        scenario_views: list[dict[str, Any]] = []

        for h in holdings:
            if agent_input.skip_recently_bought and (today - h.buy_date).days < _RECENT_DAYS:
                continue
            try:
                sig, scn = await self._evaluate(h)
            except Exception as exc:
                self._log.warning("sell_recommender_skip", ticker=h.ticker, error=str(exc))
                continue

            scenario_rows.append(scn)
            scenario_views.append({"ticker": scn.ticker, "scenario_status": scn.scenario_status})
            if sig is not None:
                sell_signals.append(sig)
                signal_views.append(
                    {"ticker": sig.ticker, "signal_type": sig.signal_type, "score": sig.score}
                )

        if not agent_input.dry_run:
            if scenario_rows:
                save_scenarios(self._ctx.engine, scenario_rows)
            if sell_signals:
                save_sell_signals(self._ctx.engine, sell_signals)

        return SellRecommenderOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=f"{len(sell_signals)} 件の売りシグナル（{len(scenario_rows)} 銘柄評価）",
            sell_signals=signal_views,
            scenario_updates=scenario_views,
        )

    async def _evaluate(self, h: Portfolio) -> tuple[SellSignal | None, Scenario]:
        current = await self._current_price(h.ticker) or h.buy_price
        rsi = await self._rsi(h.ticker)
        health, status, checklist, ai_conf = await self._scenario_eval(h)

        scenario = Scenario(
            ticker=h.ticker,
            scenario_health=health,
            scenario_status=status,
            checklist_progress=checklist,
            evaluation_notes="",
        )

        pnl = (current - h.buy_price) / h.buy_price if h.buy_price else 0.0
        if pnl >= 0:
            signal_type = "profit_taking"
            ta = target_achievement(current, h.buy_price, h.target_pct)
            tw = technical_warning(rsi)
            score = round(profit_taking_score(ta, health, tw, ai_conf))
            components = {
                "target_achievement_score": ta,
                "scenario_achievement_score": health,
                "technical_warning_score": tw,
            }
            reasons: list[dict[str, Any]] = [
                {
                    "title": "目標到達度",
                    "detail": f"目標 {h.target_pct * 100:.0f}% に対し含み {pnl * 100:.1f}%",
                }
            ]
        else:
            signal_type = "stop_loss"
            sb = 1.0 - health
            lm = loss_magnitude(current, h.buy_price, h.stop_loss_pct)
            score = round(stop_loss_score(sb, lm, 0.0, ai_conf))
            components = {
                "scenario_break_score": sb,
                "loss_magnitude_score": lm,
                "negative_news_score": 0.0,
            }
            reasons = [{"title": "含み損", "detail": f"取得比 {pnl * 100:.1f}%"}]
            if score >= _DISCIPLINE_THRESHOLD:
                reasons.extend(discipline_reasons(h.buy_price, h.stop_loss_pct, current))

        if score < _SELL_THRESHOLD:
            return None, scenario

        signal = SellSignal(
            ticker=h.ticker,
            signal_type=signal_type,
            score=score,
            ai_confidence=ai_conf,
            reasons=reasons,
            recommended_action=determine_sell_recommendation(signal_type, score, h.qty, current),
            **components,
        )
        return signal, scenario

    # --- データ取得 -----------------------------------------------------------

    def _load_holdings(self, engine: Engine, tickers: list[str] | None) -> list[Portfolio]:
        with Session(engine) as session:
            stmt = select(Portfolio).where(Portfolio.status == "active")
            holdings = list(session.exec(stmt))
        if tickers:
            holdings = [h for h in holdings if h.ticker in tickers]
        return holdings

    async def _current_price(self, ticker: str) -> float | None:
        try:
            out = await self._ctx.call_tool("market_data", MarketDataInput(tickers=[ticker]))
            payload = getattr(out, "data", None)
            if out.success and payload:
                value = payload.get(ticker, {}).get("current_price")
                return float(value) if isinstance(value, int | float) else None
        except Exception as exc:
            self._log.warning("sell_price_failed", ticker=ticker, error=str(exc))
        return None

    async def _rsi(self, ticker: str) -> float | None:
        try:
            out = await self._ctx.call_tool("technicals", TechnicalsInput(ticker=ticker))
            payload = getattr(out, "data", None)
            if out.success and payload:
                rsi = payload.get("rsi")
                return float(rsi) if isinstance(rsi, int | float) else None
        except Exception as exc:
            self._log.warning("sell_rsi_failed", ticker=ticker, error=str(exc))
        return None

    async def _scenario_eval(self, h: Portfolio) -> tuple[float, str, list[dict[str, Any]], float]:
        """thesis_checklist の進捗を LLM 評価。失敗時は health=0.5 で縮退。"""
        checklist = h.thesis_checklist or []
        prompt = (
            f"銘柄 {h.ticker} の投資仮説の進捗を評価し JSON で返してください。"
            f"thesis_checklist={checklist}。"
            "キー: checklist_progress[]（item/status/evidence/confidence）, "
            "overall_health(0-1), overall_status(intact/weakening/broken), ai_confidence(0-1)。"
        )
        try:
            out = await self._ctx.call_tool(
                "llm_call",
                LLMCallInput(
                    prompt=prompt,
                    purpose="analysis",
                    routing_hint="hot",
                    agent=self.name,
                    invocation_id=self._ctx.invocation_id,
                ),
            )
            if out.success:
                parsed = json.loads(getattr(out, "response", "") or "{}")
                health = float(parsed.get("overall_health", 0.5))
                status = str(parsed.get("overall_status") or status_from_health(health))
                progress = list(parsed.get("checklist_progress", []))
                ai_conf = float(parsed.get("ai_confidence", 0.5))
                return health, status, progress, ai_conf
        except Exception as exc:
            self._log.warning("sell_scenario_eval_failed", ticker=h.ticker, error=str(exc))
        return 0.5, status_from_health(0.5), [], 0.5
