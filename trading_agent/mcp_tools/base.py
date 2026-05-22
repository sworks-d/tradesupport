"""MCP ツールの基底クラスと共通エラーハンドリング。SYSTEM_DESIGN.md §3.1 / §3.4。

全 MCP ツールは ``MCPTool`` を継承し、中核ロジックを ``_execute`` に実装する。
公開メソッド ``execute`` が共通処理（リトライ・フォールバック・エラー分類・ロギング）を
担う。エラー方針は SYSTEM_DESIGN §3.4 に従う：

| エラー種別 | 挙動 |
|---|---|
| NETWORK_ERROR | 指数バックオフで N 回リトライ → フォールバック → 失敗出力 |
| RATE_LIMIT | retry_after を尊重して待機しリトライ → フォールバック → 失敗出力 |
| AUTH_ERROR | 即時失敗（リトライしない）。health_check が検知する想定 |
| DATA_NOT_FOUND | 「該当データなし」= 成功扱い（success=True, data=None） |
| VALIDATION_ERROR | 即時失敗（バグの可能性） |

``success=False`` は呼び出し側で判断する。``data=None`` でも ``success=True`` の場合は
「該当データなし」と区別する（§3.4）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from trading_agent.utils.logger import get_logger


class MCPErrorType(StrEnum):
    """MCP ツールのエラー分類（SYSTEM_DESIGN §3.4）。"""

    NETWORK_ERROR = "network_error"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    DATA_NOT_FOUND = "data_not_found"
    VALIDATION_ERROR = "validation_error"
    UNKNOWN = "unknown"


class MCPError(Exception):
    """MCP ツール例外の基底。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.UNKNOWN


class NetworkError(MCPError):
    """一時的なネットワーク障害（リトライ対象）。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.NETWORK_ERROR


class RateLimitError(MCPError):
    """レート制限（retry_after を尊重してリトライ）。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.RATE_LIMIT

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(MCPError):
    """認証エラー（リトライ不可、即失敗）。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.AUTH_ERROR


class DataNotFoundError(MCPError):
    """該当データなし（エラーではなく成功扱いに変換される）。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.DATA_NOT_FOUND


class ToolValidationError(MCPError):
    """入力・データの検証エラー（バグの可能性、即失敗）。"""

    error_type: ClassVar[MCPErrorType] = MCPErrorType.VALIDATION_ERROR


class MCPToolInput(BaseModel):
    """ツール入力の基底。"""


class SourceRef(BaseModel):
    """データの出典（防御層＝出典実在・時点照合の土台。B1）。

    数値・主張が「どのソースの、いつ時点のものか」をコードが保持するための最小構造。
    LLMに数値を作らせない原則（数値はコードが取得した実データのみ）の裏付けとして、
    各出力の出所を機械可読に残す。後段の防御層がこれを使って機械照合する。
    """

    source: str  # 例: "yfinance" / "edinet" / "tdnet" / "newsapi" / "computed"
    ref: str | None = None  # URL / 開示ID / 財務項目 / ticker 等の参照
    as_of: datetime | None = None  # その出典の時点（temporal hallucination 対策）
    note: str | None = None


class MCPToolOutput(BaseModel):
    """ツール出力の基底（SYSTEM_DESIGN §3.1）。"""

    success: bool
    error: str | None = None
    error_type: MCPErrorType | None = None
    data: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # --- 防御層（B1）：全ツール共通の出典・時点。既定は空で後方互換。 ---
    data_asof: datetime | None = None  # この出力データの代表時点
    source_refs: list[SourceRef] = Field(default_factory=list)  # 出典の列挙


# リトライ対象（一時的障害）
_TRANSIENT: tuple[type[MCPError], ...] = (NetworkError, RateLimitError)


class MCPTool[TInput: MCPToolInput](ABC):
    """全 MCP ツールの基底クラス。

    サブクラスは ``name`` / ``description`` / ``input_schema`` を設定し、
    ``_execute`` を実装する。一時的障害は ``NetworkError`` / ``RateLimitError``、
    認証エラーは ``AuthError``、該当なしは ``DataNotFoundError`` を送出する。
    キャッシュ等のフォールバックは ``fallback`` を上書きする。
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    input_schema: type[MCPToolInput] = MCPToolInput
    output_schema: type[MCPToolOutput] = MCPToolOutput

    # リトライ設定（【たたき台】。インスタンスごとに上書き可。settings 化は後続フェーズ）
    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 8.0

    @abstractmethod
    async def _execute(self, tool_input: TInput) -> MCPToolOutput:
        """ツールの中核ロジック。失敗は型付き例外（MCPError 系）で送出する。"""

    async def fallback(self, tool_input: TInput, error: Exception) -> MCPToolOutput | None:
        """リトライ枯渇時のフォールバック。既定は無し（None）。サブクラスで上書きする。"""
        return None

    async def health_check(self) -> bool:
        """依存先（API 等）が生きているか。既定は True。"""
        return True

    async def execute(self, tool_input: TInput) -> MCPToolOutput:
        """共通処理付きでツールを実行する（リトライ・フォールバック・エラー分類）。"""
        log = get_logger("mcp_tool").bind(tool=self.name or type(self).__name__)
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(_TRANSIENT),
            reraise=True,
        )
        try:
            result: MCPToolOutput = await retryer(self._execute, tool_input)
            log.debug("mcp_tool_success")
            return result
        except (NetworkError, RateLimitError) as exc:
            log.warning(
                "mcp_tool_transient_failed",
                error=str(exc),
                error_type=exc.error_type.value,
            )
            fb = await self.fallback(tool_input, exc)
            if fb is not None:
                log.info("mcp_tool_fallback_used")
                return fb
            return self._error_output(exc, exc.error_type)
        except AuthError as exc:
            log.error("mcp_tool_auth_error", error=str(exc))
            return self._error_output(exc, MCPErrorType.AUTH_ERROR)
        except DataNotFoundError as exc:
            # 「該当データなし」はエラーではない（§3.4）
            metadata = {"note": str(exc)} if str(exc) else {}
            return MCPToolOutput(
                success=True,
                data=None,
                error_type=MCPErrorType.DATA_NOT_FOUND,
                metadata=metadata,
            )
        except ToolValidationError as exc:
            log.error("mcp_tool_validation_error", error=str(exc))
            return self._error_output(exc, MCPErrorType.VALIDATION_ERROR)
        except Exception as exc:  # 想定外（バグの可能性）。握りつぶさず分類して返す。
            log.error(
                "mcp_tool_unexpected_error",
                error=str(exc),
                error_kind=type(exc).__name__,
            )
            return self._error_output(exc, MCPErrorType.UNKNOWN)

    def _wait(self, retry_state: RetryCallState) -> float:
        """待機時間を決める。RateLimitError は retry_after を尊重、他は指数バックオフ。"""
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RateLimitError) and exc.retry_after is not None:
            return float(exc.retry_after)
        waiter = wait_exponential(multiplier=self.backoff_base, max=self.backoff_max)
        return float(waiter(retry_state))

    def _error_output(self, error: Exception, error_type: MCPErrorType) -> MCPToolOutput:
        return MCPToolOutput(
            success=False,
            error=str(error) or type(error).__name__,
            error_type=error_type,
            data=None,
        )


class MCPHost:
    """MCP ツールの登録・取得を担うレジストリ。"""

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool[Any]] = {}

    def register(self, tool: MCPTool[Any]) -> None:
        """ツールを登録する。名前重複は ValueError。"""
        key = tool.name or type(tool).__name__
        if key in self._tools:
            raise ValueError(f"MCP ツール名が重複しています: {key}")
        self._tools[key] = tool

    def get(self, name: str) -> MCPTool[Any]:
        """名前でツールを取得する。未登録は KeyError。"""
        if name not in self._tools:
            raise KeyError(f"未登録の MCP ツール: {name}")
        return self._tools[name]

    def list_tools(self) -> list[str]:
        """登録済みツール名の一覧（ソート済み）。"""
        return sorted(self._tools)

    async def health_check_all(self) -> dict[str, bool]:
        """全ツールの health_check を実行する（例外は False に丸める）。"""
        results: dict[str, bool] = {}
        for name, tool in self._tools.items():
            try:
                results[name] = await tool.health_check()
            except Exception:
                results[name] = False
        return results
