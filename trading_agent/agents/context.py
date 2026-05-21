"""エージェントのツールアクセス層（Task 1.3.3）。

LangChain の自律ツールループは使わず、エージェントは AgentContext 経由で MCP ツールを
明示的に呼ぶ（決定論・コスト管理・テスト容易性を優先）。``call_tool`` は MCPHost を介して
ツールを実行し、戻り値は型付き ``MCPToolOutput``。LLM は ``llm_call`` ツールを使う。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from trading_agent.mcp_tools.base import MCPHost, MCPToolInput, MCPToolOutput


@dataclass
class AgentContext:
    """エージェント実行コンテキスト（ツール群 + DB + トレース ID）。"""

    host: MCPHost
    engine: Engine
    invocation_id: str

    async def call_tool(self, name: str, tool_input: MCPToolInput) -> MCPToolOutput:
        """登録済み MCP ツールを名前で実行する。"""
        return await self.host.get(name).execute(tool_input)
