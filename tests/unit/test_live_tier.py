"""段階的実弾移行のテスト（v2.10 Phase I-12）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.utils import lot_size


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch):
    """wille_settings.json を tmp_path に分離（本番設定を汚染しない）。"""
    fake = tmp_path / "wille_settings.json"
    monkeypatch.setattr(lot_size, "_BROKER_MODE_FILE", str(fake))
    yield fake


class TestGetLiveTier:
    def test_default_tier_1_when_unset(self, isolated_settings: Path, monkeypatch):
        monkeypatch.delenv("WILLE_LIVE_TIER", raising=False)
        assert lot_size.get_live_tier() == "tier_1"

    def test_env_override_takes_priority(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_3")
        assert lot_size.get_live_tier() == "tier_3"

    def test_invalid_env_falls_back(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_LIVE_TIER", "invalid")
        assert lot_size.get_live_tier() == "tier_1"

    def test_reads_from_settings_file(self, isolated_settings: Path, monkeypatch):
        monkeypatch.delenv("WILLE_LIVE_TIER", raising=False)
        isolated_settings.write_text(
            json.dumps({"live_tier": "tier_2"}), encoding="utf-8"
        )
        assert lot_size.get_live_tier() == "tier_2"


class TestSetLiveTier:
    def test_set_persists_to_file(self, isolated_settings: Path):
        lot_size.set_live_tier("tier_3")
        data = json.loads(isolated_settings.read_text(encoding="utf-8"))
        assert data["live_tier"] == "tier_3"

    def test_set_preserves_existing_keys(self, isolated_settings: Path):
        isolated_settings.write_text(
            json.dumps({"broker_mode": "live"}), encoding="utf-8"
        )
        lot_size.set_live_tier("tier_2")
        data = json.loads(isolated_settings.read_text(encoding="utf-8"))
        assert data["broker_mode"] == "live"
        assert data["live_tier"] == "tier_2"

    def test_set_rejects_invalid_tier(self, isolated_settings: Path):
        with pytest.raises(ValueError):
            lot_size.set_live_tier("tier_99")


class TestGetTierMaxJpy:
    def test_tier_1_returns_10k(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_1")
        assert lot_size.get_tier_max_jpy() == 10_000.0

    def test_tier_4_returns_1m(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_4")
        assert lot_size.get_tier_max_jpy() == 1_000_000.0

    def test_full_returns_none(self, isolated_settings: Path, monkeypatch):
        monkeypatch.setenv("WILLE_LIVE_TIER", "full")
        assert lot_size.get_tier_max_jpy() is None


class TestGetMaxLotCostJpyWithTier:
    def test_paper_mode_ignores_tier(self, isolated_settings: Path, monkeypatch):
        """paper モード（is_moomoo_live=False）は tier 無視で従来通り。"""
        monkeypatch.delenv("WILLE_PAPER_MODE", raising=False)
        monkeypatch.delenv("WILLE_BROKER_LIVE", raising=False)
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_1")
        # broker=paper（デフォルト）→ tier 無視
        # 1M 預金で従来 25% = 250k が返るはず（10k に絞られない）
        result = lot_size.get_max_lot_cost_jpy(1_000_000)
        assert result == 250_000.0

    def test_live_mode_caps_at_tier_1(self, isolated_settings: Path, monkeypatch):
        """broker=live なら tier_1 (¥10k) を上限として効かせる。"""
        monkeypatch.delenv("WILLE_PAPER_MODE", raising=False)  # lot mode 有効化
        monkeypatch.setenv("WILLE_BROKER_LIVE", "1")
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_1")
        result = lot_size.get_max_lot_cost_jpy(1_000_000)
        # 通常なら 25% = 250k だが tier_1=10k で min されて 10k
        assert result == 10_000.0

    def test_live_mode_caps_at_tier_3(self, isolated_settings: Path, monkeypatch):
        monkeypatch.delenv("WILLE_PAPER_MODE", raising=False)
        monkeypatch.setenv("WILLE_BROKER_LIVE", "1")
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_3")
        result = lot_size.get_max_lot_cost_jpy(1_000_000)
        assert result == 200_000.0

    def test_live_mode_full_tier_no_cap(self, isolated_settings: Path, monkeypatch):
        """full tier は従来ロジックで返る。"""
        monkeypatch.delenv("WILLE_PAPER_MODE", raising=False)
        monkeypatch.setenv("WILLE_BROKER_LIVE", "1")
        monkeypatch.setenv("WILLE_LIVE_TIER", "full")
        result = lot_size.get_max_lot_cost_jpy(1_000_000)
        assert result == 250_000.0  # 25% lot_pct ベース

    def test_env_max_lot_jpy_still_capped_by_tier(self, isolated_settings: Path, monkeypatch):
        """WILLE_MAX_LOT_JPY 直接指定でも tier 上限が効く。"""
        monkeypatch.delenv("WILLE_PAPER_MODE", raising=False)
        monkeypatch.setenv("WILLE_BROKER_LIVE", "1")
        monkeypatch.setenv("WILLE_LIVE_TIER", "tier_2")
        monkeypatch.setenv("WILLE_MAX_LOT_JPY", "500000")
        result = lot_size.get_max_lot_cost_jpy(10_000_000)
        # tier_2=50k で 500k から絞られる
        assert result == 50_000.0
