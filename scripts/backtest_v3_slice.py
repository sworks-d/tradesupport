"""BT-1 CLI: v3 価格スライス backtest を J-Quants Free データで実行（決定論コア・財務OFF版）。

  .venv/bin/python scripts/backtest_v3_slice.py [--n 30] [--start 2025-01-06] [--end 2025-12-01]

J-Quants Free 制約:
  - 直近 12 週は遅延で取得不可 → --end は十分過去（既定 2025-12-01）に。
  - 履歴 2 年 → --start も 2 年以内。warm-up に start の ~70 取引日前から取得する。
  - survivorship: 現 active universe を使う（「current universe / survivorship biased」と明記）。

結果は増額判断(ゲート⑥)の証明ではなく、規律・選定ロジックの退行検出/粗い筋確認。
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.backtest.quote_cache import _valid_price, cached_codes, load_quotes, store_quotes
from trading_agent.backtest.v3_slice import BacktestConfig, run_price_slice_backtest
from trading_agent.db import get_engine
from trading_agent.mcp_tools.jquants import get_default_client
from trading_agent.models.universe import Universe
from trading_agent.utils.ticker_normalize import universe_to_jquants

_MIN_COVERAGE = 10  # これ未満は INVALID_DATA_COVERAGE（backtest 信号として読まない）
_MIN_TRADES = 10    # これ未満は INVALID_SAMPLE_SIZE（成績解釈禁止）

_MARKET_ETF = "1306"  # TOPIX 連動 ETF（相対力の分母）


def _arg(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def _to_date(v) -> dt.date | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v)
    try:
        return dt.date.fromisoformat(s.replace("/", "-")[:10])
    except ValueError:
        digits = "".join(ch for ch in s if ch.isdigit())[:8]
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])) if len(digits) == 8 else None


def _fetch_quotes(client, ticker: str, frm: dt.date, to: dt.date) -> list[tuple[dt.date, float]]:
    """J-Quants daily_quotes → [(date, AdjC), ...古→新]。AdjC が無ければ C にフォールバック。"""
    jq = universe_to_jquants(ticker)
    rows = client.daily_quotes(jq, from_date=frm, to_date=to)
    out: list[tuple[dt.date, float]] = []
    for r in rows:
        d = _to_date(r.get("Date"))
        adj = r.get("AdjC", r.get("AdjustmentClose", r.get("C", r.get("Close"))))
        v = _valid_price(adj)  # 有限 ∧ 正のみ（NaN/inf/0以下 を入口で弾く・store と同基準）
        if d is None or v is None:
            continue
        out.append((d, v))
    out.sort(key=lambda x: x[0])
    return out


def _cache_sufficient(cached: list[tuple[dt.date, float]], frm: dt.date, to: dt.date) -> bool:
    """cache が要求期間を両端までカバーしているか（codex 指摘 2: len>63 だけだと片寄り見逃し）。

    RS に足る本数(>63) かつ 開始/終了近辺（±10 日）まで価格があることを要求する。
    片側に偏った cache を「十分」と誤判定して欠損のまま backtest しないため。
    """
    if len(cached) <= 63:
        return False
    dates = [d for d, _ in cached]
    near = dt.timedelta(days=10)
    return min(dates) <= frm + near and max(dates) >= to - near


def _load_or_fetch(
    client, code: str, frm: dt.date, to: dt.date, *,
    cache_only: bool, max_fetch_left: int, throttle: float,
) -> tuple[list[tuple[dt.date, float]], bool, int]:
    """cache-first で価格を返す。cache が両端までカバーしていれば API を叩かない。
    Returns: (quotes, rate_limited, max_fetch_left)。rate_limited=True なら呼び出し側は以降の fetch を止める。
    """
    cached = load_quotes(code, frm, to)
    if _cache_sufficient(cached, frm, to) or cache_only or max_fetch_left <= 0:
        return cached, False, max_fetch_left
    # cache 不足 & API 余地あり → fetch（throttle）
    time.sleep(throttle)
    q = _fetch_quotes(client, code, frm, to)
    if q:
        store_quotes(code, q, now=dt.date.today().isoformat())
        return q, False, max_fetch_left - 1
    # 空 = レート枯渇/データ無し。区別できないが、stop-on-rate-limit で安全側に止める。
    return cached, True, max_fetch_left - 1


def main() -> int:
    n = int(_arg("--n", "30"))
    start = _to_date(_arg("--start", "2025-01-06")) or dt.date(2025, 1, 6)
    end = _to_date(_arg("--end", "2025-12-01")) or dt.date(2025, 12, 1)
    throttle = float(_arg("--throttle", "3.0"))   # Free は重めが既定（codex: 0.5 は軽すぎ）
    max_fetch = int(_arg("--max-fetch", "5"))      # 1 run の API 対象上限（Free 保護）
    cache_only = "--cache-only" in sys.argv        # API を一切叩かず cache だけで backtest
    warmup_from = start - dt.timedelta(days=130)

    client = get_default_client()
    if client is None and not cache_only:
        print("J-Quants クライアント取得失敗（JQUANTS_REFRESH_TOKEN 未設定）。--cache-only なら cache で実行可")
        return 1

    engine = get_engine(Path("data") / "trading.sqlite")
    with Session(engine) as s:
        rows = s.exec(
            select(Universe).where(col(Universe.is_active))
            .where(col(Universe.market) == "JP")
            .order_by(col(Universe.market_cap_jpy).asc().nullslast())
        ).all()
    tickers = [u.ticker for u in rows if len(u.ticker) == 4 and u.ticker.isdigit()][:n]
    have = cached_codes()
    print(f"universe={len(tickers)} 件（JP4桁・小型優先・survivorship biased）/ cache 済={len(have & set(tickers))} 件 "
          f"/ cache-only={cache_only} max-fetch={max_fetch} throttle={throttle}s")

    # codex 指摘 1: market fetch も budget から消費（--max-fetch 0 = API ゼロ呼出の直感に合わせる）
    budget = max_fetch
    market, rl, budget = _load_or_fetch(client, _MARKET_ETF, warmup_from, end,
                                        cache_only=cache_only, max_fetch_left=budget, throttle=throttle)
    if not market:
        if cache_only:
            why = "cache に市場ベンチ未蓄積。先に --max-fetch≥1 で 1306 を取得してから --cache-only を。"
        elif max_fetch <= 0:
            why = "--max-fetch 0（API 抑止指定）で cache にも無し。--max-fetch≥1 か別日で。"
        else:
            why = "fetch 失敗/レート枯渇。同日は追加 fetch せず、別日に --max-fetch≥1 で。"
        print(f"市場ベンチ {_MARKET_ETF} 取得不可: {why}")
        return 1

    quotes_by_ticker: dict[str, list[tuple[dt.date, float]]] = {}
    fetched = failed = 0
    for t in tickers:
        q, rl, budget = _load_or_fetch(client, t, warmup_from, end,
                                       cache_only=cache_only, max_fetch_left=budget, throttle=throttle)
        if len(q) > 63:
            quotes_by_ticker[t] = q
            fetched += 1
        else:
            failed += 1
        if rl:  # 429/枯渇 → 以降の fetch を止める（codex: stop-on-rate-limit）
            print("⚠ レート枯渇/取得失敗を検知 → 以降の fetch を停止（cache 分で続行）")
            break
    print(f"backtest 対象（cache+今回fetch）={fetched} 件 / 不足/失敗={failed} / 市場={len(market)} 本")

    # === status 3 分類（codex Q3: 誤読防止）=================================
    # INVALID_DATA_COVERAGE: 取得銘柄 <10 → backtest でなくデータ可用性レポート
    # INVALID_SAMPLE_SIZE  : 銘柄は足りるが確定取引 <10 → 成績解釈禁止
    # VALID_SMOKE          : 両方 ≥10 → スモークとして読める（※ゲート⑥証明ではない）
    if fetched < _MIN_COVERAGE:
        print(f"\n[STATUS] INVALID_DATA_COVERAGE（取得 {fetched} < {_MIN_COVERAGE}）= NOT A BACKTEST SIGNAL。")
        print("   backtest 結果ではなく『データ可用性レポート』。同日は欲張らず、別日に")
        print("   --max-fetch 5 --throttle 4 を数回 → cache 蓄積 → --cache-only で再実行。")
        return 0

    cfg = BacktestConfig(start=start, end=end)
    res = run_price_slice_backtest(quotes_by_ticker, market, cfg)
    print()
    print(res.summary())
    if res.n_trades < _MIN_TRADES:
        print(f"\n[STATUS] INVALID_SAMPLE_SIZE（確定取引 {res.n_trades} < {_MIN_TRADES}）= NOT A BACKTEST SIGNAL。")
        print("   coverage は足りるが取引が薄い。期間延長 or 銘柄数増（cache 蓄積後）で取引数を確保してから成績解釈する。")
    else:
        print(f"\n[STATUS] VALID_SMOKE（coverage {fetched}≥{_MIN_COVERAGE} ∧ 取引 {res.n_trades}≥{_MIN_TRADES}）"
              "= スモークとして読める。※ゲート⑥（増額判断）の証明ではない（survivorship/Free薄サンプル/財務&LLM OFF）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
