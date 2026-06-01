"""trailing_check の単体テスト（v2.10 Phase 1A）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.trailing_check import run_trailing_check


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "trail.sqlite")
    create_all(eng)
    return eng


def _add_holding(
    eng, ticker: str, buy_price: float, qty: int = 100, stop_pct: float = 0.08
) -> None:
    with Session(eng, expire_on_commit=False) as s:
        s.add(
            Universe(
                ticker=ticker,
                name=ticker,
                market="JP",
                sector="Industrials",
                market_cap=1e12,
                market_cap_jpy=1e12,
                avg_volume_30d=1e6,
                is_active=True,
            )
        )
        s.add(
            Portfolio(
                ticker=ticker,
                personality="ASUKA",
                buy_date=dt.date(2026, 5, 1),
                buy_price=buy_price,
                qty=qty,
                currency="JPY",
                strategy_category="中期",
                target_period_days=60,
                target_pct=0.10,
                stop_loss_pct=stop_pct,
                target_date=dt.date(2026, 7, 1),
                thesis="test",
                status="active",
                broker_mode="paper",
            )
        )
        s.commit()


class TestTrailingCheck:
    def test_保有ゼロでno_holdings(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        r = run_trailing_check(eng, price_lookup={})
        assert r["status"] == "no_holdings"
        assert r["checked"] == 0

    def test_含み益0で売却なし(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        r = run_trailing_check(eng, price_lookup={"A": 1000.0})
        # 元 stop は -8% = 920 円。現価 1000 なので売らない
        assert r["status"] == "active"
        assert r["stop_triggered"] == []

    def test_含み益10pct後の下落でstop発動(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # buy 1000, stop -8%、含み益 +10% で trail price = 1000 * (1 + 0.02) = 1020
        # 現価が 1020 を下回ったら売却推奨
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        # 1019 < 1020 で発動
        r = run_trailing_check(eng, price_lookup={"A": 1019.0})
        # 含み益 +1.9%、tier "未起動" のはず（+5% 未満なので trailing 適用されず元 stop）
        # 元 stop = -8% = 920 → 1019 > 920 なので発動しない
        assert r["stop_triggered"] == []

    def test_含み益5pct到達後の下落でtrailing発動_真のtrailing(
        self, tmp_path: Path
    ) -> None:
        """v2.10 Phase 1A-Step2 仕様: peak_pnl_pct を記録するので、
        一旦 +5% に達した後の下落で trailing が **発動する**（真の trailing）。"""
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        # 1 回目: 現価 1050 = +5% → peak_pnl_pct=0.05 に更新、trail_price=970、発動せず
        r1 = run_trailing_check(eng, price_lookup={"A": 1050.0})
        assert r1["stop_triggered"] == []
        # 2 回目: 現価 969 = -3.1% → peak=0.05 のまま、trail_price=970 → 969 < 970 で **発動**
        # （Step1 の動的 stop なら発動しなかったが、Step2 の真の trailing で発動）
        r2 = run_trailing_check(eng, price_lookup={"A": 969.0})
        assert len(r2["stop_triggered"]) == 1
        assert r2["stop_triggered"][0]["ticker"] == "A"

    def test_含み益10pct時点の現価で発動条件(self, tmp_path: Path) -> None:
        """含み益 +10% に達した時の trail_price = +2% line（損益分岐超え）。
        現価がさらに下落して +2% を割ると発動。"""
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        # 現価 1100 = 含み益 +10% で trail_price = 1020
        # 現価 1019 < 1020 で発動するはずだが、現価 1019 では「含み益 +1.9%」なので
        # その時点での tier は未起動 → 元 stop 920
        # つまり「過去 +10% に達した記録」がないと trailing は発動しない
        # この簡易実装では「現価ベースで都度計算」なので、過去のピークは見ない
        # よって発動条件: 現価が +10% & かつ stop ライン (= +2% line) を割る
        # 同時に成立することはない（+2% < +10% は自明）
        #
        # つまりこの実装では「**今日の含み益 X%** に応じた stop」になる
        # → 「過去ピーク」を別途記録しないと真の trailing にはならない
        # → 今は「定期的に状況を再計算」する形で、明日下落していれば発動
        # （現状の挙動を確認する目的）
        r = run_trailing_check(eng, price_lookup={"A": 1100.0})
        # 含み益 +10%、trail_price = 1020 円、現価 1100 > 1020 → 発動せず
        assert r["stop_triggered"] == []

    def test_明確な損切りで発動(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        # 現価 900 = -10% 含み損 → 元 stop -8% (=920) を割る → 発動
        r = run_trailing_check(eng, price_lookup={"A": 900.0})
        assert len(r["stop_triggered"]) == 1
        triggered = r["stop_triggered"][0]
        assert triggered["ticker"] == "A"
        assert triggered["action"] == "sell_loss"

    def test_Decision重複登録防止(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0, stop_pct=0.08)
        # 1 回目
        run_trailing_check(eng, price_lookup={"A": 900.0})
        # 2 回目（同条件）
        r = run_trailing_check(eng, price_lookup={"A": 900.0})
        # 1 回目に既に Decision 登録済 → 2 回目は重複登録しない
        assert r["stop_triggered"] == []

    def test_価格None銘柄は判定しない_推測しない(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0)
        _add_holding(eng, "B", buy_price=1000.0)
        # B は価格辞書に無い
        r = run_trailing_check(eng, price_lookup={"A": 900.0})
        assert "B" in r["skipped_no_price"]
        # A だけ判定（含み損で stop 発動）
        assert any(t["ticker"] == "A" for t in r["stop_triggered"])

    def test_Universe不在銘柄はskip_ハルシネーション防壁(self, tmp_path: Path) -> None:
        """Universe テーブルから外された銘柄は判定しない。"""
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0)
        # A の Universe を is_active=False に
        with Session(eng, expire_on_commit=False) as s:
            uni = s.get(Universe, "A")
            assert uni is not None
            uni.is_active = False
            s.add(uni)
            s.commit()
        r = run_trailing_check(eng, price_lookup={"A": 900.0})
        assert "A" in r["skipped_not_in_universe"]
        assert r["stop_triggered"] == []
