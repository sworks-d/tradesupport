"""二次価格ソース _live_secondary の anti-bot 検知 統合テスト。ネット非依存（httpx monkeypatch）。

stooq が rate-limit/bot 検知時に返す /__verify HTML を価格としてパースせず、誤った値を
reconcile に混入させないことを固定する（master タスク）。
"""

from __future__ import annotations

from scripts.build_snapshot import _live_secondary


class _FakeResp:
    def __init__(self, text: str, content_type: str = "text/csv") -> None:
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:  # noqa: D401
        return None


def _patch_httpx(monkeypatch, resp: _FakeResp) -> None:
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)


def test_live_secondary_skips_anti_bot_html(monkeypatch) -> None:
    """anti-bot HTML(/__verify JS) は価格化せず空（誤値を入れない）。"""
    _patch_httpx(
        monkeypatch,
        _FakeResp(
            "<!DOCTYPE html><html><script>location='/__verify'</script></html>",
            content_type="text/html",
        ),
    )
    assert _live_secondary(["7203"]) == {}


def test_live_secondary_parses_valid_csv(monkeypatch) -> None:
    """正常な CSV は従来どおり価格化する（回帰なし）。"""
    csv = (
        "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        "7203.JP,2026-06-06,15:00,100,110,95,105,1000"
    )
    _patch_httpx(monkeypatch, _FakeResp(csv, content_type="text/csv"))
    out = _live_secondary(["7203"])
    assert "7203" in out
    assert out["7203"]["current_price"] == 105.0


def test_live_secondary_skips_non_numeric_close(monkeypatch) -> None:
    """Close が数値でない応答は価格化しない（推測しない）。"""
    bad = (
        "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        "7203.JP,2026-06-06,15:00,100,110,95,N/D,1000"
    )
    _patch_httpx(monkeypatch, _FakeResp(bad, content_type="text/csv"))
    assert _live_secondary(["7203"]) == {}
