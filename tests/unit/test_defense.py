"""防御層（機械照合・決裁前ゲート）の単体テスト（B3）。

出典・時点が付いていれば照合OK、欠ければ未照合（赤）。割れ/na/未照合なら既定「保留」。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.magi import verify
from trading_agent.models.magi import JudgeVerdict

_NOW = datetime(2026, 5, 22)


def _v(
    judge: str, verdict: str, *, refs: bool = True, asof: datetime | None = _NOW
) -> JudgeVerdict:
    return JudgeVerdict(
        ticker="NVDA",
        judge=judge,
        verdict=verdict,
        confidence="中",
        reason="x",
        source_refs=[{"source": "yfinance", "ref": "NVDA", "as_of": "2026-05-22"}] if refs else [],
        data_asof=asof,
    )


def test_unanimous_buy_with_provenance_not_held() -> None:
    res = verify(
        [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")], now=_NOW
    )
    assert res.figures_checked is True
    assert res.time_ok is True
    assert res.default_hold is False  # 全会一致買い＋照合済 → 保留に寄せない


def test_split_defaults_to_hold() -> None:
    res = verify(
        [_v("MELCHIOR", "buy"), _v("BALTHASAR", "hold"), _v("CASPER", "warn")], now=_NOW
    )
    assert res.default_hold is True  # 割れ → 既定保留


def test_na_defaults_to_hold() -> None:
    res = verify(
        [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "na")], now=_NOW
    )
    assert res.default_hold is True  # 判定不能を含む → 既定保留


def test_missing_provenance_flags_unverified_and_holds() -> None:
    res = verify(
        [_v("MELCHIOR", "buy", refs=False), _v("BALTHASAR", "buy"), _v("CASPER", "buy")],
        now=_NOW,
    )
    assert res.figures_checked is False
    assert any("出典なし" in c for c in res.unverified_claims)
    assert res.default_hold is True  # 未照合 → 既定保留


def test_missing_asof_flags_time() -> None:
    res = verify(
        [_v("MELCHIOR", "buy", asof=None), _v("BALTHASAR", "buy"), _v("CASPER", "buy")],
        now=_NOW,
    )
    assert res.time_ok is False
    assert any("時点なし" in c for c in res.unverified_claims)


def test_stale_data_noted() -> None:
    old = _NOW - timedelta(days=500)
    res = verify(
        [_v("MELCHIOR", "buy", asof=old), _v("BALTHASAR", "buy"), _v("CASPER", "buy")],
        now=_NOW,
    )
    assert any("古い" in n for n in res.notes)


def test_balthasar_vote_excluded_from_gate() -> None:
    # 業績・文脈が買い＋照合済 → 株価(BALTHASAR)が hold でも既定保留にしない（投票外）
    res = verify([_v("MELCHIOR", "buy"), _v("BALTHASAR", "hold"), _v("CASPER", "buy")], now=_NOW)
    assert res.default_hold is False
    # 投票審判(文脈)が na なら、株価が buy でも保留（投票審判のnaは効く）
    res2 = verify([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "na")], now=_NOW)
    assert res2.default_hold is True
