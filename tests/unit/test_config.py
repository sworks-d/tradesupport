"""config.py の単体テスト（Task 1.0.2）。

外部 ``.env`` に依存しないよう、構築時は ``_env_file=None`` を渡し、
必須項目は明示的に与える。``get_settings`` のテストでは env を monkeypatch する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.config import (
    ConfigError,
    Settings,
    get_settings,
    load_settings,
)

# すべての必須項目を満たす最小セット
_REQUIRED_KWARGS = {
    "anthropic_api_key": "sk-ant-test",
    "moomoo_trading_pwd": "pwd",
    "moomoo_account_id": "acct",
}


def _make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**_REQUIRED_KWARGS, **overrides})  # type: ignore[arg-type]


class TestDefaults:
    def test_defaults_applied(self) -> None:
        settings = _make_settings()
        assert settings.trading_mode == "paper"
        assert settings.moomoo_opend_host == "localhost"
        assert settings.moomoo_opend_port == 11111
        assert settings.ollama_model == "llama3.1:8b"
        assert settings.macos_notification is True
        assert settings.newsapi_key is None

    def test_required_values_set(self) -> None:
        settings = _make_settings()
        assert settings.anthropic_api_key == "sk-ant-test"
        assert settings.moomoo_trading_pwd == "pwd"
        assert settings.moomoo_account_id == "acct"


class TestPathExpansion:
    def test_tilde_expanded(self) -> None:
        settings = _make_settings()
        for path in (settings.db_path, settings.log_dir, settings.data_dir, settings.halt_file):
            assert "~" not in str(path)
            assert path.is_absolute()

    def test_explicit_path_expanded(self) -> None:
        settings = _make_settings(db_path=Path("~/custom/db.sqlite"))
        assert str(settings.db_path).startswith(str(Path.home()))


class TestLogLevel:
    def test_lowercase_normalized(self) -> None:
        assert _make_settings(log_level="debug").log_level == "DEBUG"

    def test_invalid_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            _make_settings(log_level="verbose")


class TestTradingMode:
    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            _make_settings(trading_mode="demo")


class TestEnsureDirectories:
    def test_creates_directories(self, tmp_path: Path) -> None:
        settings = _make_settings(
            db_path=tmp_path / "sub" / "db.sqlite",
            log_dir=tmp_path / "logs",
            data_dir=tmp_path / "data",
        )
        settings.ensure_directories()
        assert settings.db_path.parent.is_dir()
        assert settings.log_dir.is_dir()
        assert settings.data_dir.is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        settings = _make_settings(log_dir=tmp_path / "logs", data_dir=tmp_path / "data")
        settings.ensure_directories()
        settings.ensure_directories()  # 2回目もエラーにならない
        assert settings.log_dir.is_dir()


class TestLoadSettings:
    def test_missing_required_raises_config_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("ANTHROPIC_API_KEY", "MOOMOO_TRADING_PWD", "MOOMOO_ACCOUNT_ID"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(ConfigError) as exc_info:
            load_settings(_env_file=None)
        message = str(exc_info.value)
        # 欠落フィールド名が含まれ、.env への導線がある
        assert "anthropic_api_key" in message
        assert ".env" in message

    def test_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        monkeypatch.setenv("MOOMOO_TRADING_PWD", "envpwd")
        monkeypatch.setenv("MOOMOO_ACCOUNT_ID", "envacct")
        settings = load_settings(_env_file=None)
        assert settings.anthropic_api_key == "sk-ant-env"
        assert settings.moomoo_account_id == "envacct"


class TestGetSettings:
    def test_cached_and_creates_dirs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        monkeypatch.setenv("MOOMOO_TRADING_PWD", "envpwd")
        monkeypatch.setenv("MOOMOO_ACCOUNT_ID", "envacct")
        monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
        get_settings.cache_clear()
        try:
            first = get_settings()
            second = get_settings()
            assert first is second  # キャッシュされている
            assert (tmp_path / "logs").is_dir()
            assert (tmp_path / "data").is_dir()
        finally:
            get_settings.cache_clear()
