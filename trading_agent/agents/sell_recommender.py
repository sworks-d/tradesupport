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
from trading_agent.utils.time_utils import today_jst, utcnow

# v2.2 TASK-SR1: 機別の「買って直後は売らない」最小保有日数
# horizon の 5% を min_holding_days として使う（KAWORU 21d → 1日 / REI 180d → 9日）
_MIN_HOLDING_RATIO = 0.05
_RECENT_DAYS_DEFAULT = 3  # personality 情報が取れない時のフォールバック


def _min_holding_days_for(personality: str | None, target_period_days: int | None) -> int:
    """機別の min_holding_days（horizon * 5%）。"""
    if target_period_days is None or target_period_days <= 0:
        return _RECENT_DAYS_DEFAULT
    return max(1, int(target_period_days * _MIN_HOLDING_RATIO))


class SellRecommenderInput(AgentInput):
    tickers: list[str] | None = None
    skip_recently_bought: bool = True


class SellRecommenderOutput(AgentOutput):
    sell_signals: list[dict[str, Any]] = Field(default_factory=list)
    scenario_updates: list[dict[str, Any]] = Field(default_factory=list)


# ---- スコア成分（純粋関数。0-1 を返す） ------------------------------------


def loss_magnitude(current: float, buy: float, stop_loss_pct: float) -> float:
    """v2.1 TASK-SZ4: stop_loss_pct は正値前提（0.15 = -15%）。

    返り値: 0.0（含み益 or 損失なし）〜 1.0（stop ライン到達 or 超え）。
    """
    if buy <= 0 or stop_loss_pct <= 0:
        return 0.0
    loss = (current - buy) / buy  # 負値（損失時）
    if loss >= 0:
        return 0.0
    # loss=-0.15, stop_loss_pct=0.15 → magnitude=1.0
    return max(0.0, min(-loss / stop_loss_pct, 1.0))


# v2.4 TASK-SR3: stop_loss_score の重みを定数化（実証データで校正予定）
# 根拠（暫定）:
#   - scenario_break (0.4): シナリオ無効化が最重要シグナル
#   - loss_mag (0.3): 損失幅は規律発動の主要 trigger
#   - negative_news (0.2): ニュース影響は補助
#   - ai_confidence (0.1): LLM 自信度は最弱（実証データ蓄積で重み調整）
_SELL_SCORE_WEIGHTS = {
    "scenario_break": 0.4,
    "loss_mag": 0.3,
    "negative_news": 0.2,
    "ai_confidence": 0.1,
}


def stop_loss_score(
    scenario_break: float, loss_mag: float, negative_news: float, ai_confidence: float
) -> float:
    w = _SELL_SCORE_WEIGHTS
    return (
        scenario_break * w["scenario_break"]
        + loss_mag * w["loss_mag"]
        + negative_news * w["negative_news"]
        + ai_confidence * w["ai_confidence"]
    ) * 100


# v2.5 TASK-SR6: status_from_health の閾値を定数化（環境変数で上書き可）
import os as _os_sr
_HEALTH_INTACT_TH = float(_os_sr.environ.get("HEALTH_INTACT_TH", "0.7"))
_HEALTH_WEAKENING_TH = float(_os_sr.environ.get("HEALTH_WEAKENING_TH", "0.4"))


def status_from_health(health: float) -> str:
    if health >= _HEALTH_INTACT_TH:
        return "intact"
    if health >= _HEALTH_WEAKENING_TH:
        return "weakening"
    return "broken"


def determine_sell_recommendation(
    signal_type: str,
    qty: int,
    current_price: float,
    *,
    is_jp: bool = True,
) -> dict[str, Any]:
    """全量手仕舞いの推奨（B'）。利確で刻まず、サイズを減らす出口は固定stopと保有期限のみ。

    v2.2 TASK-SR4: time_exit の指値を市場別に動的化（流動性想定）。
    - JP: -0.3%（旧 0.997）
    - US: -0.15%（米国は流動性が高い）
    """
    if signal_type == "stop_loss":
        return {
            "type": "market",
            "qty": qty,
            "qty_label": "全量（成行）",
            "note": "損切り（規律・固定stop到達）",
        }
    # time_exit（保有期限到達）：全量を指値で手仕舞い
    spread = 0.997 if is_jp else 0.9985
    return {
        "type": "limit",
        "price": round(current_price * spread, 4),
        "qty": qty,
        "qty_label": "全量",
        "note": f"保有期限到達（{'JP -0.3%' if is_jp else 'US -0.15%'}指値）",
    }


def discipline_reasons(
    buy_price: float, stop_loss_pct: float, current_price: float
) -> list[dict[str, Any]]:
    """固定stop到達時の規律メッセージ（FX 経験を踏まえた設計）。

    v2.1 TASK-SZ4: stop_loss_pct は正値前提（0.15 = -15%）。
    """
    stop_price = buy_price * (1 - stop_loss_pct)
    return [
        {
            "title": "事前に決めた損切りラインに到達",
            "detail": (
                f"取得 {buy_price} / 損切りライン -{stop_loss_pct * 100:.0f}%"
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
        today = today_jst()

        sell_signals: list[SellSignal] = []
        scenario_rows: list[Scenario] = []
        signal_views: list[dict[str, Any]] = []
        scenario_views: list[dict[str, Any]] = []

        for h in holdings:
            # v2.2 TASK-SR1: 機別の min_holding_days で判定
            min_days = _min_holding_days_for(h.personality, h.target_period_days)
            if agent_input.skip_recently_bought and (today - h.buy_date).days < min_days:
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
        # v2.1 TASK-SZ4: stop_loss_pct は正値（例 0.15）、pnl は負値（例 -0.15）。
        # stop 到達条件: pnl <= -stop_loss_pct
        if pnl <= -h.stop_loss_pct:
            sb = 1.0 - health
            lm = loss_magnitude(current, h.buy_price, h.stop_loss_pct)
            signal = SellSignal(
                ticker=h.ticker,
                signal_type="stop_loss",
                # v2.5 TASK-SR7: negative_news は現在未配線（0.0 固定）。
                # 将来は CASPER のネガ語数や news_score を渡す hook を追加。
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
                recommended_action=determine_sell_recommendation(
                    "stop_loss", h.qty, current, is_jp=h.currency == "JPY"
                ),
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
                recommended_action=determine_sell_recommendation(
                    "time_exit", h.qty, current, is_jp=h.currency == "JPY"
                ),
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
        """thesis_checklist の進捗を LLM 評価。

        v2.2 TASK-SR2: 失敗時 health=0.5 の偽装をやめる。
        代わりに health=None (= "unknown") + status="unknown" を返し、stop/time-exit の
        物理判定のみで売り判断する（シナリオ評価には依存しない）。
        """
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
        # v2.2 TASK-SR2: 失敗時は「不明」を返す。stop/time-exit の物理判定だけが効く。
        # 後段で health の数値が必要な計算（loss_magnitude_score 等）には 0.0 を渡す
        # （= 「不明」を「悪化」と同じ扱いにしない＝過敏な売りシグナルを出さない）
        return 0.0, "unknown", [], 0.0
