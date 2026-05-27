"""sell-recommender エージェント（出口設計 = B'：2026-05-24 改訂）。

保有銘柄を毎朝チェックし、**全量手仕舞い**のみを推奨する。サイズを減らす出口は2つだけ：
- **固定stop（下方向・機械優先）**：取得比が事前に決めた stop_loss_pct（仮説無効化価格）に到達したら
  全量・成行で損切り。stop到達は規律の瞬間なので、常に規律メッセージを付す（FX経験を踏まえた設計）。
- **保有期限 time-exit**：target_date 到達で全量手仕舞い（中期投資の保有上限）。

**利確（profit-taking）でサイズを刻むのは廃止**（旧：score≥50で半量／≥90で全量）。バックテスト実測で、
利確キャップが達成可能収益の約2/3を破壊していたため（offense-edge スカウト 2026-05-24）。勝ちは
固定stop か 保有期限まで放任する＝「下方向は機械優先・上方向は放任」という非対称な出口設計。
- シナリオ進捗（thesis_checklist 評価）は LLM。未登録/失敗時は health=0.5 で縮退。
"""

from __future__ import annotations

import datetime as dt
from trading_agent.llm.json_extract import extract_json
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.agents.serialization import save_scenarios, save_sell_signals
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.mcp_tools.market_data import MarketDataInput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import Scenario, SellSignal
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_RECENT_DAYS = 3


class SellRecommenderInput(AgentInput):
    tickers: list[str] | None = None
    skip_recently_bought: bool = True


class SellRecommenderOutput(AgentOutput):
    sell_signals: list[dict[str, Any]] = Field(default_factory=list)
    scenario_updates: list[dict[str, Any]] = Field(default_factory=list)


# ---- スコア成分（純粋関数。0-1 を返す） ------------------------------------


def loss_magnitude(current: float, buy: float, stop_loss_pct: float) -> float:
    if buy <= 0 or stop_loss_pct >= 0:
        return 0.0
    loss = (current - buy) / buy
    if loss >= 0:
        return 0.0
    return max(0.0, min(loss / stop_loss_pct, 1.0))  # stop ライン到達で 1.0


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
    signal_type: str, qty: int, current_price: float
) -> dict[str, Any]:
    """全量手仕舞いの推奨（B'）。利確で刻まず、サイズを減らす出口は固定stopと保有期限のみ。"""
    if signal_type == "stop_loss":
        return {
            "type": "market",
            "qty": qty,
            "qty_label": "全量（成行）",
            "note": "損切り（規律・固定stop到達）",
        }
    # time_exit（保有期限到達）：全量を指値で手仕舞い
    return {
        "type": "limit",
        "price": round(current_price * 0.997, 4),
        "qty": qty,
        "qty_label": "全量",
        "note": "保有期限到達",
    }


def discipline_reasons(
    buy_price: float, stop_loss_pct: float, current_price: float
) -> list[dict[str, Any]]:
    """固定stop到達時の規律メッセージ（FX 経験を踏まえた設計）。"""
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
    """保有銘柄の出口（固定stop / 保有期限）推奨エージェント。利確では刻まない（勝ち放任）。"""

    name = "sell_recommender"
    description = "保有の出口（固定stop/保有期限）を全量手仕舞いで出力する。"
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
                sig, scn = await self._evaluate(h, today)
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

    async def _evaluate(self, h: Portfolio, today: dt.date) -> tuple[SellSignal | None, Scenario]:
        current = await self._current_price(h.ticker) or h.buy_price
        health, status, checklist, ai_conf = await self._scenario_eval(h)

        scenario = Scenario(
            ticker=h.ticker,
            scenario_health=health,
            scenario_status=status,
            checklist_progress=checklist,
            evaluation_notes="",
        )

        pnl = (current - h.buy_price) / h.buy_price if h.buy_price else 0.0

        # 下方向＝事前に決めた固定stop（仮説無効化価格）到達で機械的に全量損切り（最優先）。
        if pnl <= h.stop_loss_pct:
            sb = 1.0 - health
            lm = loss_magnitude(current, h.buy_price, h.stop_loss_pct)
            signal = SellSignal(
                ticker=h.ticker,
                signal_type="stop_loss",
                score=round(stop_loss_score(sb, lm, 0.0, ai_conf)),
                ai_confidence=ai_conf,
                scenario_break_score=sb,
                loss_magnitude_score=lm,
                negative_news_score=0.0,
                reasons=[
                    {
                        "title": "含み損",
                        "detail": f"取得比 {pnl * 100:.1f}% で固定stop到達",
                    },
                    *discipline_reasons(h.buy_price, h.stop_loss_pct, current),
                ],
                recommended_action=determine_sell_recommendation("stop_loss", h.qty, current),
            )
            return signal, scenario

        # 保有期限（target_date）到達＝time-exit。勝ち放任の上限として全量手仕舞い。
        if today >= h.target_date:
            signal = SellSignal(
                ticker=h.ticker,
                signal_type="time_exit",
                score=0,
                ai_confidence=ai_conf,
                reasons=[
                    {
                        "title": "保有期限到達",
                        "detail": (
                            f"target_date {h.target_date} 到達。取得比 {pnl * 100:.1f}%。"
                            "期限で全量手仕舞い（利確では刻まない＝勝ち放任）。"
                        ),
                    }
                ],
                recommended_action=determine_sell_recommendation("time_exit", h.qty, current),
            )
            return signal, scenario

        # stop未到達かつ期限前＝勝ちは放任。利確シグナルは出さない。
        return None, scenario

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
                parsed = extract_json(getattr(out, "response", None))
                health = float(parsed.get("overall_health", 0.5))
                status = str(parsed.get("overall_status") or status_from_health(health))
                progress = list(parsed.get("checklist_progress", []))
                ai_conf = float(parsed.get("ai_confidence", 0.5))
                return health, status, progress, ai_conf
        except Exception as exc:
            self._log.warning("sell_scenario_eval_failed", ticker=h.ticker, error=str(exc))
        return 0.5, status_from_health(0.5), [], 0.5
