"""ピラミッディング戦略の単体テスト（v2.10 Phase 2）。"""

from __future__ import annotations

import pytest

from trading_agent.portfolio.pyramiding import (
    get_initial_alloc,
    get_pyramid_stages,
    get_stage_label,
    get_target_alloc,
    should_pyramid_up,
)


class TestPyramidStages:
    def test_REI_3段階(self) -> None:
        stages = get_pyramid_stages("REI")
        assert len(stages) == 3
        assert stages[0].cumulative_alloc == 0.40
        assert stages[1].cumulative_alloc == 0.70
        assert stages[2].cumulative_alloc == 1.00
        # トリガー
        assert stages[1].trigger_pnl_pct == 0.05
        assert stages[2].trigger_pnl_pct == 0.10

    def test_ASUKA_2段階(self) -> None:
        stages = get_pyramid_stages("ASUKA")
        assert len(stages) == 2
        assert stages[0].cumulative_alloc == 0.50
        assert stages[1].cumulative_alloc == 1.00

    def test_KAWORU_1段階(self) -> None:
        stages = get_pyramid_stages("KAWORU")
        assert len(stages) == 1
        assert stages[0].cumulative_alloc == 1.00

    def test_不明な機はKAWORUとして扱う_保守的(self) -> None:
        stages = get_pyramid_stages("UNKNOWN")
        assert len(stages) == 1  # KAWORU と同じ
        assert stages[0].cumulative_alloc == 1.00


class TestInitialAlloc:
    @pytest.mark.parametrize(
        "pilot,expected",
        [
            ("REI", 0.40),
            ("ASUKA", 0.50),
            ("SHINJI", 0.50),
            ("KAWORU", 1.00),
        ],
    )
    def test_機別初期比率(self, pilot: str, expected: float) -> None:
        assert get_initial_alloc(pilot) == expected


class TestTargetAlloc:
    def test_REI_含み益5pctで70(self) -> None:
        assert get_target_alloc("REI", 0.05) == 0.70

    def test_REI_含み益10pctで100(self) -> None:
        assert get_target_alloc("REI", 0.10) == 1.00

    def test_REI_含み益0pctで40(self) -> None:
        assert get_target_alloc("REI", 0.0) == 0.40

    def test_REI_含み益マイナスで初期40のまま(self) -> None:
        assert get_target_alloc("REI", -0.03) == 0.40

    def test_ASUKA_含み益5pctで満玉(self) -> None:
        assert get_target_alloc("ASUKA", 0.05) == 1.00

    def test_KAWORU_常に100_買い増ししない(self) -> None:
        assert get_target_alloc("KAWORU", 0.0) == 1.00
        assert get_target_alloc("KAWORU", 0.20) == 1.00

    def test_含み益None_は_None_を返す_推測しない(self) -> None:
        assert get_target_alloc("REI", None) is None
        assert get_target_alloc("ASUKA", None) is None


class TestShouldPyramidUp:
    def test_REI_初期40で含み益5pct_買い増し70へ(self) -> None:
        flag, target = should_pyramid_up("REI", current_alloc=0.40, current_pnl_pct=0.05)
        assert flag is True
        assert target == 0.70

    def test_REI_既に70で含み益5pct_据え置き(self) -> None:
        flag, target = should_pyramid_up("REI", current_alloc=0.70, current_pnl_pct=0.05)
        assert flag is False
        assert target == 0.70

    def test_境界ぎりぎり_推測せず保留(self) -> None:
        """target が current より 0.5% 以下しか上でないなら買い増ししない。"""
        # current=0.70, target=0.70（マージン内）
        flag, _ = should_pyramid_up("REI", current_alloc=0.700, current_pnl_pct=0.05)
        assert flag is False
        # current=0.701, target=0.70（マージン内）
        flag, _ = should_pyramid_up("REI", current_alloc=0.701, current_pnl_pct=0.05)
        assert flag is False

    def test_含み益None_買い増ししない(self) -> None:
        flag, target = should_pyramid_up("REI", current_alloc=0.40, current_pnl_pct=None)
        assert flag is False
        assert target == 0.40


class TestStageLabel:
    def test_REI_初期(self) -> None:
        assert "1/3" in get_stage_label("REI", 0.0)

    def test_REI_2段階目(self) -> None:
        assert "2/3" in get_stage_label("REI", 0.05)

    def test_REI_満玉(self) -> None:
        label = get_stage_label("REI", 0.10)
        assert "満玉" in label or "3/3" in label

    def test_含み益None_は_data_na(self) -> None:
        assert "n/a" in get_stage_label("REI", None)
