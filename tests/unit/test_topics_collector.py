"""topics-collector エージェントの単体テスト（Task 1.4.1）。

news / disclosure / llm_call はモックツールを MCPHost に登録して注入。DB は一時 SQLite。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.topics_collector import (
    TopicsCollectorAgent,
    TopicsCollectorInput,
    extract_affected_tickers,
    rule_based_importance,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.disclosure import DisclosureInput, DisclosureOutput
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.mcp_tools.news import NewsInput, NewsOutput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.topics import Topic


class _NewsStub(MCPTool[NewsInput]):
    name = "news"

    def __init__(self, articles: list[dict], ok: bool = True) -> None:
        self._articles = articles
        self._ok = ok

    async def _execute(self, tool_input: NewsInput) -> NewsOutput:
        return NewsOutput(success=self._ok, articles=self._articles)


class _DisclosureStub(MCPTool[DisclosureInput]):
    name = "disclosure"

    def __init__(self, disclosures: list[dict]) -> None:
        self._disclosures = disclosures

    async def _execute(self, tool_input: DisclosureInput) -> DisclosureOutput:
        return DisclosureOutput(success=True, disclosures=self._disclosures)


class _LLMStub(MCPTool[LLMCallInput]):
    name = "llm_call"

    def __init__(self, importance: str = "medium") -> None:
        self._importance = importance

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        return LLMCallOutput(success=True, response=f'{{"importance": "{self._importance}"}}')


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "topics.sqlite")
    create_all(eng)
    with Session(eng) as s:
        s.add(
            Portfolio(
                ticker="AAPL",
                buy_date=dt.date(2026, 1, 1),
                buy_price=180.0,
                qty=1,
                currency="USD",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.1,
                stop_loss_pct=-0.08,
                target_date=dt.date(2026, 4, 1),
                thesis="t",
                status="active",
            )
        )
        s.commit()
    return eng


def _ctx(tmp_path: Path, news: list[dict], disc: list[dict], llm_importance: str = "medium"):
    host = MCPHost()
    host.register(_NewsStub(news))
    host.register(_DisclosureStub(disc))
    host.register(_LLMStub(llm_importance))
    return AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")


class TestHelpers:
    def test_extract_explicit_and_known(self) -> None:
        # v2.1 TASK-S2: known_tickers でフィルタするので、AAPL が universe にあれば通る
        out = extract_affected_tickers("$AAPL up, トヨタ (7203) も", {"7203", "AAPL"})
        assert "AAPL" in out and "7203" in out

    def test_extract_filters_false_positives(self) -> None:
        # v2.1 TASK-S2: "USA"・"BUY"・"2024" 等の false positive を弾く
        text = "USA economy improves, but no BUY signal yet. In 2024, market grew."
        out = extract_affected_tickers(text, {"USA", "BUY", "2024"})
        # 明示記法 ($XXX, (NNNN), NNNN.T) が無いので 1 件も拾わない
        assert out == []

    def test_rule_importance_portfolio_is_high(self) -> None:
        item = {"title": "x", "summary": "", "source_type": "news"}
        assert rule_based_importance(item, ["AAPL"], {"AAPL"}, set()) == "high"

    def test_rule_importance_keyword_high(self) -> None:
        item = {"title": "FOMC で利上げ", "summary": "", "source_type": "news"}
        assert rule_based_importance(item, [], set(), set()) == "high"


class TestAgent:
    async def test_collects_and_classifies(self, tmp_path: Path) -> None:
        news = [
            {
                "title": "FRB 利下げ観測",
                "summary": "",
                "url": "http://n/1",
                "source": "BB",
                "published_at": "",
            },
            {
                "title": "$AAPL strong quarter",
                "summary": "",
                "url": "http://n/2",
                "source": "RT",
                "published_at": "",
            },
            {
                "title": "FRB 利下げ観測",
                "summary": "",
                "url": "http://n/1",
                "source": "BB",
                "published_at": "",
            },  # dup url
        ]
        disc = [
            {
                "ticker": "7203",
                "title": "トヨタ 決算速報",
                "url": "http://d/1",
                "source": "TDnet",
                "published_at": "",
            },
        ]
        ctx = _ctx(tmp_path, news, disc)
        agent = TopicsCollectorAgent(ctx)
        out = await agent.execute(TopicsCollectorInput(invocation_id="inv"))

        assert out.success is True
        with Session(ctx.engine) as s:
            topics = s.exec(select(Topic)).all()
        assert len(topics) == 3  # 重複 url が1件除外
        headlines = {t.headline for t in topics}
        assert "トヨタ 決算速報" in headlines
        aapl = next(t for t in topics if "AAPL" in t.headline)
        assert aapl.importance == "high"  # 保有銘柄 AAPL に影響
        toyota = next(t for t in topics if "トヨタ" in t.headline)
        assert toyota.importance == "high"  # 決算速報

    async def test_llm_circuit_breaker_trips_after_repeated_empty_response(
        self, tmp_path: Path
    ) -> None:
        """LLM が空応答を連発したら、6件目以降は LLM を呼ばずルールベースで進む。"""

        class _FailingLLM(MCPTool[LLMCallInput]):
            name = "llm_call"

            def __init__(self) -> None:
                self.call_count = 0

            async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
                self.call_count += 1
                # 空応答 → extract_json は {} を返す → reinforce 失敗とカウント
                return LLMCallOutput(success=True, response="")

        host = MCPHost()
        # 10件の "low" 重要度ニュース（LLM 補強が走る対象）
        news = [
            {
                "title": f"地味な記事 {i}",
                "summary": "",
                "url": f"http://n/{i}",
                "source": "X",
                "published_at": "",
            }
            for i in range(10)
        ]
        host.register(_NewsStub(news))
        host.register(_DisclosureStub([]))
        failing_llm = _FailingLLM()
        host.register(failing_llm)
        ctx = AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")

        out = await TopicsCollectorAgent(ctx).execute(TopicsCollectorInput(invocation_id="inv"))
        assert out.success is True
        # 閾値（_LLM_FAILURE_TRIP=5）を超えた後はスキップされる
        # 最初の5件で連続失敗 → 6件目で circuit open → 以降は呼ばれない
        assert failing_llm.call_count == 5, f"expected 5, got {failing_llm.call_count}"

    async def test_llm_call_cap_limits_calls_per_batch(self, tmp_path: Path) -> None:
        """1バッチで LLM 補強上限（_LLM_CALL_CAP_PER_BATCH=30）を超えると以降スキップ。"""

        class _CountingLLM(MCPTool[LLMCallInput]):
            name = "llm_call"

            def __init__(self) -> None:
                self.call_count = 0

            async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
                self.call_count += 1
                return LLMCallOutput(success=True, response='{"importance": "medium"}')

        host = MCPHost()
        # 50件の低重要度ニュース（全て LLM 補強対象）
        news = [
            {
                "title": f"地味な記事 {i}",
                "summary": "",
                "url": f"http://n/{i}",
                "source": "X",
                "published_at": "",
            }
            for i in range(50)
        ]
        host.register(_NewsStub(news))
        host.register(_DisclosureStub([]))
        counting_llm = _CountingLLM()
        host.register(counting_llm)
        ctx = AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")

        out = await TopicsCollectorAgent(ctx).execute(TopicsCollectorInput(invocation_id="inv"))
        assert out.success is True
        # 上限 30 件で打ち止め（残り20件はルールベースのまま）
        assert counting_llm.call_count == 30

    async def test_llm_reinforces_low(self, tmp_path: Path) -> None:
        news = [
            {
                "title": "地味な小ネタ",
                "summary": "特に何も",
                "url": "http://n/1",
                "source": "X",
                "published_at": "",
            },
        ]
        ctx = _ctx(tmp_path, news, [], llm_importance="medium")
        out = await TopicsCollectorAgent(ctx).execute(TopicsCollectorInput(invocation_id="inv"))
        assert out.success is True
        with Session(ctx.engine) as s:
            topic = s.exec(select(Topic)).one()
        assert topic.importance == "medium"
        assert topic.importance_judged_by == "llm"

    async def test_news_failure_is_graceful(self, tmp_path: Path) -> None:
        host = MCPHost()
        host.register(_NewsStub([], ok=False))  # news 失敗
        host.register(
            _DisclosureStub(
                [
                    {
                        "ticker": "7203",
                        "title": "決算速報",
                        "url": "http://d/1",
                        "source": "TDnet",
                        "published_at": "",
                    }
                ]
            )
        )
        host.register(_LLMStub())
        ctx = AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")
        out = await TopicsCollectorAgent(ctx).execute(TopicsCollectorInput(invocation_id="inv"))
        assert out.success is True
        assert out.topics_added == 1  # disclosure のみ
