"""broker_provider 設計のテスト（v2.10 単元未満株対応）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.utils import lot_size


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch):
    fake = tmp_path / "wille_settings.json"
    monkeypatch.setattr(lot_size, "_BROKER_MODE_FILE", str(fake))
    yield fake


class TestGetBrokerProvider:
    def test_default_moomoo(self, isolated_settings: Path, monkeypatch):
        monkeypatch.delenv("WILLE_BROKER_PROVIDER", raising=False)
        assert lot_size.get_broker_provider() == "moomoo"

    def test_env_override(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_BROKER_PROVIDER", "sbi")
        assert lot_size.get_broker_provider() == "sbi"

    def test_invalid_env_falls_back_to_default(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_BROKER_PROVIDER", "invalid")
        assert lot_size.get_broker_provider() == "moomoo"

    def test_reads_from_settings(self, isolated_settings: Path, monkeypatch):
        monkeypatch.delenv("WILLE_BROKER_PROVIDER", raising=False)
        isolated_settings.write_text(json.dumps({"broker_provider": "rakuten"}))
        assert lot_size.get_broker_provider() == "rakuten"


class TestSetBrokerProvider:
    def test_persists(self, isolated_settings: Path):
        lot_size.set_broker_provider("sbi")
        data = json.loads(isolated_settings.read_text(encoding="utf-8"))
        assert data["broker_provider"] == "sbi"

    def test_preserves_existing_keys(self, isolated_settings: Path):
        isolated_settings.write_text(json.dumps({"broker_mode": "paper"}))
        lot_size.set_broker_provider("monex")
        data = json.loads(isolated_settings.read_text(encoding="utf-8"))
        assert data["broker_mode"] == "paper"
        assert data["broker_provider"] == "monex"

    def test_rejects_invalid(self, isolated_settings: Path):
        with pytest.raises(ValueError):
            lot_size.set_broker_provider("kabucom")  # 未対応


class TestSupportsFractionalShares:
    @pytest.mark.parametrize("provider,expected", [
        ("moomoo", False),
        ("sbi", True),
        ("rakuten", True),
        ("monex", True),
        ("fractional", True),
    ])
    def test_per_provider(self, provider: str, expected: bool):
        assert lot_size.supports_fractional_shares(provider) is expected


class TestGetLotSizeWithProvider:
    @pytest.mark.parametrize("ticker,provider,expected", [
        # moomoo（既存挙動）
        ("7203", "moomoo", 100),     # JP 旧型 → 100
        ("141A", "moomoo", 1),       # 新型 ticker は isdigit() でないので 1（既存仕様）
        ("AAPL", "moomoo", 1),       # 米国 → 1

        # sbi / fractional（単元未満株）
        ("7203", "sbi", 1),          # JP でも 1 株
        ("7203", "fractional", 1),
        ("141A", "sbi", 1),
        ("AAPL", "sbi", 1),

        # rakuten / monex
        ("9432", "rakuten", 1),
        ("9432", "monex", 1),
    ])
    def test_lot_size_per_provider(self, ticker: str, provider: str, expected: int):
        assert lot_size.get_lot_size(ticker, provider=provider) == expected

    def test_default_uses_get_broker_provider(self, isolated_settings: Path, monkeypatch):
        """provider 引数を渡さない時は get_broker_provider() を参照。"""
        monkeypatch.setenv("WILLE_BROKER_PROVIDER", "sbi")
        # JP 銘柄でも SBI なら 1 株
        assert lot_size.get_lot_size("7203") == 1
        monkeypatch.setenv("WILLE_BROKER_PROVIDER", "moomoo")
        assert lot_size.get_lot_size("7203") == 100
