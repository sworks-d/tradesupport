"""DAG エンジンの単体テスト（Task 1.5.1）。"""

from __future__ import annotations

import asyncio

import pytest

from trading_agent.orchestrator.dag import (
    DAGExecutor,
    DAGNode,
    OrchestrationError,
    overall_status,
)


class TestExecute:
    async def test_runs_in_dependency_order(self) -> None:
        order: list[str] = []

        def make(name: str):
            async def f() -> str:
                order.append(name)
                return name

            return f

        nodes = [
            DAGNode("a", make("a")),
            DAGNode("b", make("b"), depends_on=["a"]),
            DAGNode("c", make("c"), depends_on=["b"]),
        ]
        results = await DAGExecutor(nodes).execute()
        assert order == ["a", "b", "c"]
        assert all(r.status == "success" for r in results.values())

    async def test_failure_is_captured_and_continues(self) -> None:
        ran: list[str] = []

        async def boom() -> None:
            raise RuntimeError("kaboom")

        async def after() -> str:
            ran.append("after")
            return "ok"

        nodes = [
            DAGNode("boom", boom),
            DAGNode("after", after, depends_on=["boom"]),  # 依存先失敗でも実行
        ]
        results = await DAGExecutor(nodes).execute()
        assert results["boom"].status == "failure"
        assert results["after"].status == "success"
        assert ran == ["after"]

    async def test_timeout(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(1)

        results = await DAGExecutor([DAGNode("slow", slow, timeout_s=0.01)]).execute()
        assert results["slow"].status == "timeout"

    async def test_circular_raises(self) -> None:
        async def noop() -> None:
            return None

        nodes = [
            DAGNode("a", noop, depends_on=["b"]),
            DAGNode("b", noop, depends_on=["a"]),
        ]
        with pytest.raises(OrchestrationError):
            await DAGExecutor(nodes).execute()


class TestOverallStatus:
    def test_all_success(self) -> None:
        from trading_agent.orchestrator.dag import NodeResult

        r = {"a": NodeResult("a", "success"), "b": NodeResult("b", "success")}
        assert overall_status(r) == "success"

    def test_partial(self) -> None:
        from trading_agent.orchestrator.dag import NodeResult

        r = {"a": NodeResult("a", "success"), "b": NodeResult("b", "failure")}
        assert overall_status(r) == "partial"

    def test_failed(self) -> None:
        from trading_agent.orchestrator.dag import NodeResult

        r = {"a": NodeResult("a", "failure")}
        assert overall_status(r) == "failed"
