"""軽量 DAG エンジン（ORCHESTRATION.md §2.3 / A-1・A-2）。

依存関係を解決しつつ、実行可能なノードを並列実行する。LangGraph は使わない（Phase 1）。
ノードの失敗・タイムアウトは捕捉して記録し、後続ノードは継続する（Graceful Degradation §A-3）。
依存は「実行順序」のみを表し、依存先の成否に関わらず後続は実行される（DB 経由でデータ受け渡し）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from trading_agent.utils.logger import get_logger

_log = get_logger("orchestrator")

NodeFunc = Callable[[], Awaitable[Any]]


class OrchestrationError(RuntimeError):
    """DAG 構造の不正（循環依存・到達不能）。"""


@dataclass
class DAGNode:
    name: str
    func: NodeFunc
    depends_on: list[str] = field(default_factory=list)
    timeout_s: float = 300.0


@dataclass
class NodeResult:
    name: str
    status: str  # "success" / "failure" / "timeout"
    result: Any = None
    error: str | None = None


class DAGExecutor:
    """DAG を依存順 + 部分並列で実行する。"""

    def __init__(self, nodes: list[DAGNode]) -> None:
        self._nodes = {n.name: n for n in nodes}

    async def execute(self) -> dict[str, NodeResult]:
        results: dict[str, NodeResult] = {}
        executed: set[str] = set()

        while len(executed) < len(self._nodes):
            ready = [
                n
                for name, n in self._nodes.items()
                if name not in executed and all(dep in executed for dep in n.depends_on)
            ]
            if not ready:
                stuck = sorted(set(self._nodes) - executed)
                raise OrchestrationError(f"循環依存または到達不能なノード: {stuck}")

            outcomes = await asyncio.gather(*(self._run_node(n) for n in ready))
            for nr in outcomes:
                results[nr.name] = nr
                executed.add(nr.name)

        return results

    async def _run_node(self, node: DAGNode) -> NodeResult:
        log = _log.bind(node=node.name)
        try:
            result = await asyncio.wait_for(node.func(), timeout=node.timeout_s)
            log.info("dag_node_success")
            return NodeResult(node.name, "success", result=result)
        except TimeoutError:
            log.error("dag_node_timeout", timeout_s=node.timeout_s)
            return NodeResult(node.name, "timeout", error=f"timed out after {node.timeout_s}s")
        except Exception as exc:  # 1ノードの失敗で全体を止めない
            log.exception("dag_node_failed", error=str(exc))
            return NodeResult(node.name, "failure", error=str(exc))


def overall_status(results: dict[str, NodeResult]) -> str:
    """ノード結果から全体ステータスを判定する。"""
    statuses = {nr.status for nr in results.values()}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses:
        return "partial"
    return "failed"
