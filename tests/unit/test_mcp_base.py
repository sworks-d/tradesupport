"""mcp_tools/base.py の単体テスト（Task 1.1.1）。"""

from __future__ import annotations

import pytest

from trading_agent.mcp_tools.base import (
    AuthError,
    DataNotFoundError,
    MCPErrorType,
    MCPHost,
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
    RateLimitError,
)


class _Input(MCPToolInput):
    value: int = 0


class _OkTool(MCPTool[_Input]):
    name = "ok_tool"
    description = "always ok"
    input_schema = _Input
    backoff_base = 0.0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        return MCPToolOutput(success=True, data=tool_input.value * 2)


class _FlakyTool(MCPTool[_Input]):
    """succeed_on 回目の呼び出しで成功する。それ未満は NetworkError。"""

    name = "flaky"
    backoff_base = 0.0

    def __init__(self, succeed_on: int) -> None:
        self.succeed_on = succeed_on
        self.calls = 0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        self.calls += 1
        if self.calls < self.succeed_on:
            raise NetworkError(f"temp failure {self.calls}")
        return MCPToolOutput(success=True, data="recovered")


class _FallbackTool(MCPTool[_Input]):
    name = "fallback_tool"
    backoff_base = 0.0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        raise NetworkError("always down")

    async def fallback(self, tool_input: _Input, error: Exception) -> MCPToolOutput | None:
        return MCPToolOutput(success=True, data="from_cache", metadata={"degraded": True})


class _AuthTool(MCPTool[_Input]):
    name = "auth_tool"
    backoff_base = 0.0

    def __init__(self) -> None:
        self.calls = 0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        self.calls += 1
        raise AuthError("bad key")


class _NotFoundTool(MCPTool[_Input]):
    name = "not_found"
    backoff_base = 0.0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        raise DataNotFoundError("no rows")


class _RateLimitTool(MCPTool[_Input]):
    name = "rate_limited"
    backoff_base = 0.0

    def __init__(self, succeed_on: int) -> None:
        self.succeed_on = succeed_on
        self.calls = 0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        self.calls += 1
        if self.calls < self.succeed_on:
            raise RateLimitError("slow down", retry_after=0.0)
        return MCPToolOutput(success=True, data="ok")


class _BoomTool(MCPTool[_Input]):
    name = "boom"
    backoff_base = 0.0

    async def _execute(self, tool_input: _Input) -> MCPToolOutput:
        raise RuntimeError("unexpected bug")


class TestExecuteHappyPath:
    async def test_success(self) -> None:
        out = await _OkTool().execute(_Input(value=5))
        assert out.success is True
        assert out.data == 10
        assert out.error is None

    async def test_retry_then_success(self) -> None:
        tool = _FlakyTool(succeed_on=2)
        out = await tool.execute(_Input())
        assert out.success is True
        assert out.data == "recovered"
        assert tool.calls == 2  # 1回失敗 → 2回目成功


class TestExecuteErrors:
    async def test_retry_exhausted_no_fallback(self) -> None:
        tool = _FlakyTool(succeed_on=99)  # 決して成功しない
        out = await tool.execute(_Input())
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
        assert tool.calls == 3  # max_attempts

    async def test_fallback_used_on_exhaustion(self) -> None:
        out = await _FallbackTool().execute(_Input())
        assert out.success is True
        assert out.data == "from_cache"
        assert out.metadata == {"degraded": True}

    async def test_auth_error_not_retried(self) -> None:
        tool = _AuthTool()
        out = await tool.execute(_Input())
        assert out.success is False
        assert out.error_type == MCPErrorType.AUTH_ERROR
        assert tool.calls == 1  # リトライしない

    async def test_data_not_found_is_success(self) -> None:
        out = await _NotFoundTool().execute(_Input())
        assert out.success is True
        assert out.data is None
        assert out.error_type == MCPErrorType.DATA_NOT_FOUND

    async def test_rate_limit_retry_then_success(self) -> None:
        tool = _RateLimitTool(succeed_on=2)
        out = await tool.execute(_Input())
        assert out.success is True
        assert tool.calls == 2

    async def test_unexpected_error_is_classified(self) -> None:
        out = await _BoomTool().execute(_Input())
        assert out.success is False
        assert out.error_type == MCPErrorType.UNKNOWN
        assert "unexpected bug" in (out.error or "")


class TestMCPHost:
    def test_register_and_get(self) -> None:
        host = MCPHost()
        tool = _OkTool()
        host.register(tool)
        assert host.get("ok_tool") is tool

    def test_list_tools_sorted(self) -> None:
        host = MCPHost()
        host.register(_OkTool())
        host.register(_FallbackTool())
        assert host.list_tools() == ["fallback_tool", "ok_tool"]

    def test_duplicate_registration_raises(self) -> None:
        host = MCPHost()
        host.register(_OkTool())
        with pytest.raises(ValueError):
            host.register(_OkTool())

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            MCPHost().get("nope")

    async def test_health_check_all(self) -> None:
        class _Healthy(_OkTool):
            name = "healthy"

        class _Sick(_OkTool):
            name = "sick"

            async def health_check(self) -> bool:
                raise NetworkError("down")

        host = MCPHost()
        host.register(_Healthy())
        host.register(_Sick())
        results = await host.health_check_all()
        assert results == {"healthy": True, "sick": False}
