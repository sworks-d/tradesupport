"""アプリケーション設定の読み込み（SYSTEM_DESIGN.md §6）。

設定は3層（SYSTEM_DESIGN.md §B-6）：
- 環境変数：上書き用
- ``.env``：個人開発の中心（このモジュールが扱う）
- ``settings`` テーブル：UI から変更できる動的設定（別モジュール）

このモジュールは ``.env`` / 環境変数を ``Settings`` に読み込み、
必須項目の欠落を分かりやすいエラーにして、``~/.trading-agent/`` を自動作成する。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """設定読み込みの失敗（必須項目欠落など）を表す。"""


class Settings(BaseSettings):
    """``.env`` / 環境変数から読み込む静的設定。

    フィールド名（snake_case）が環境変数名（大文字）に対応する。
    例：``anthropic_api_key`` ← ``ANTHROPIC_API_KEY``（大文字小文字は区別しない）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # デフォルト値にも field_validator を適用する（~ 展開・log_level 正規化が
        # env 未指定時にも効くようにするため。pydantic v2 は既定でデフォルトを検証しない）。
        validate_default=True,
    )

    # === API キー（必須） ===
    anthropic_api_key: str

    # === オプション API キー ===
    newsapi_key: str | None = None
    jquants_refresh_token: str | None = None
    edinet_api_key: str | None = None

    # === moomoo ===
    moomoo_opend_host: str = "localhost"
    moomoo_opend_port: int = 11111
    moomoo_trading_pwd: str
    moomoo_account_id: str
    moomoo_security_firm: str = "FUTUJP"  # moomoo JP（US主体口座なら FUTUINC）
    moomoo_markets: str = "JP,US"  # カンマ区切り。位置情報取得対象の市場（D-25: JP主軸90%・US ETFサテライト10%）

    # === モード ===
    trading_mode: Literal["paper", "live"] = "paper"
    log_level: str = "INFO"

    # === ペーパー運用の仮想残高 ===
    # `trading_mode=paper` の時、moomoo の実残高に上乗せする仮想入金（実弾化したら
    # 実際に入金して overlay は無効化＝live モードでは加算しない）。D-23 元本 ¥100,000。
    paper_overlay_cash_jpy: int = 100_000

    # === パス ===
    db_path: Path = Path("~/.trading-agent/db.sqlite")
    log_dir: Path = Path("~/.trading-agent/logs")
    data_dir: Path = Path("~/.trading-agent/data")

    # === LLM ===
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # === 通知（オプション） ===
    slack_webhook_url: str | None = None
    macos_notification: bool = True

    # === 緊急停止 ===
    halt_file: Path = Path("~/.trading-agent/HALT")

    @field_validator("db_path", "log_dir", "data_dir", "halt_file", mode="after")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        """``~`` をホームディレクトリに展開する。"""
        return value.expanduser()

    @field_validator("log_level", mode="after")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        """ログレベルを大文字に正規化し、妥当性を検証する。"""
        normalized = value.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in valid:
            raise ValueError(f"log_level は {sorted(valid)} のいずれか。受領: {value!r}")
        return normalized

    def ensure_directories(self) -> None:
        """``~/.trading-agent/`` 配下の必要ディレクトリを作成する（冪等）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


# 必須フィールド（欠落時に分かりやすく案内するため明示）
_REQUIRED_FIELDS = ("anthropic_api_key", "moomoo_trading_pwd", "moomoo_account_id")


def load_settings(_env_file: str | None = ".env") -> Settings:
    """``Settings`` を構築する。必須項目欠落時は ``ConfigError`` に変換する。

    Args:
        _env_file: 読み込む env ファイル。``None`` でファイルを無視（テスト用）。

    Returns:
        構築済み ``Settings``。

    Raises:
        ConfigError: 必須項目の欠落、または値の検証エラー。
    """
    try:
        return Settings(_env_file=_env_file)
    except ValidationError as exc:
        missing = [str(err["loc"][0]) for err in exc.errors() if err.get("type") == "missing"]
        if missing:
            raise ConfigError(
                "必須の設定が不足しています: "
                + ", ".join(missing)
                + "。`.env` を確認してください（`cp .env.example .env` でテンプレートを作成）。"
            ) from exc
        raise ConfigError(f"設定の検証に失敗しました: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス全体で共有する ``Settings`` を返す（初回構築をキャッシュ）。

    初回呼び出し時に ``~/.trading-agent/`` 配下のディレクトリを自動作成する。
    テストでは ``get_settings.cache_clear()`` でキャッシュをリセットできる。
    """
    settings = load_settings()
    settings.ensure_directories()
    return settings
