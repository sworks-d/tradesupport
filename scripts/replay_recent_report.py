"""R0: 直近 N 日の「実システム実ログ」診断レポート（read-only・¥0・replay ではない）。

実行: .venv/bin/python scripts/replay_recent_report.py [--days 14]

codex 段階設計 R0: replay 機を新設する前に、実際にシステムが出した判断ログ
（decisions / portfolio / forward / score / tag）を読みやすく集計する。
『過去N日をタイムマシン的に見たい』意図を、look-ahead リスクほぼ無し・LLM 非関与で満たす。

出すもの:
  ① 日別 decision 件数（status 別）
  ② official paper の実約定（entry 済）
  ③ 実約定 pick の sector / size 偏り（Universe join）
  ④ fundamental_event_score バケット分布（採点済のみ）
  ⑤ signal_tag 発火（entry_signal_tags 別）
  ⑥ forward マーク（最新 autoreport/forward/*.json の overall + by_score_bucket）

DB と JSON を read するのみ。売買・gate・status を一切変えない。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

from sqlmodel import Session, select

from trading_agent.db import get_engine
from trading_agent.evaluation.official_sources import OFFICIAL_FILL_SOURCES
from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.screening.event_score import bucket_event_score
from trading_agent.utils.time_utils import today_jst
from trading_agent.wille.opportunity_fill import size_bucket


def _arg_days(default: int = 14) -> int:
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return default


def _line(ch: str = "─", n: int = 60) -> str:
    return ch * n


def main() -> None:
    days = _arg_days()
    engine = get_engine("data/trading.sqlite")
    cutoff = today_jst() - dt.timedelta(days=days)

    with Session(engine) as s:
        decs = list(s.exec(select(Decision).where(Decision.date >= cutoff)).all())
        uni = {u.ticker: u for u in s.exec(select(Universe)).all()}

    print(_line("="))
    print(f"R0 実ログ診断レポート（直近 {days} 日・{cutoff}〜{today_jst()}・read-only）")
    print(_line("="))
    print(f"対象 decision: {len(decs)} 件")

    # ① 日別 status
    print("\n① 日別 decision 件数（status 別）")
    by_day: dict[str, Counter] = {}
    for d in decs:
        by_day.setdefault(str(d.date), Counter())[d.status] += 1
    for day in sorted(by_day):
        row = ", ".join(f"{k}={v}" for k, v in sorted(by_day[day].items()))
        print(f"  {day}: {row}")
    print(f"  ※ cancelled の多くは manual モードの未約定繰越 → pre_check の stale auto-cancel")

    # ② official paper 実約定（entry 済）
    entered = [
        d for d in decs
        if d.entry_date is not None
        and d.filled_via in OFFICIAL_FILL_SOURCES
        and d.entry_broker_mode == "paper"
    ]
    print(f"\n② official paper 実約定（entry 済）: {len(entered)} 件")
    for d in sorted(entered, key=lambda x: str(x.entry_date)):
        nm = uni.get(d.ticker).name if uni.get(d.ticker) else "?"
        print(f"  {d.entry_date} {d.ticker} {nm} via={d.filled_via} stance={d.gendo_stance or '-'}")

    # ③ sector / size 偏り（実約定 pick）
    print("\n③ 実約定 pick の偏り（Universe join）")
    sec = Counter()
    siz = Counter()
    for d in entered:
        u = uni.get(d.ticker)
        sec[(u.sector if u and u.sector else "不明")] += 1
        siz[size_bucket(u.market_cap_jpy if u else None)] += 1
    print(f"  sector: {dict(sec.most_common())}")
    print(f"  size:   {dict(siz.most_common())}  (large≥¥1兆 / mid ¥3000億-1兆 / small<)")

    # ④ score バケット分布（採点済）
    print("\n④ fundamental_event_score バケット分布（採点済のみ）")
    bk = Counter()
    for d in decs:
        sc = getattr(d, "fundamental_event_score", None)
        if sc is None:
            continue
        bk[bucket_event_score(sc, getattr(d, "event_score_version", None)).bucket] += 1
    print(f"  {dict(bk.most_common()) or '(採点済なし)'}")
    print(f"  ※ 旧データはタグ無=50(mid)中心。高/低差は新規イベントタグ付き蓄積待ち。")

    # ⑤ signal_tag 発火
    print("\n⑤ signal_tag 発火（entry_signal_tags 別）")
    tags = Counter()
    for d in decs:
        for t in (d.entry_signal_tags or []):
            tags[t] += 1
    print(f"  {dict(tags.most_common()) or '(発火なし)'}")

    # ⑥ forward マーク（最新 JSON）
    print("\n⑥ forward マーク（最新 autoreport/forward/*.json）")
    fdir = Path("autoreport/forward")
    files = sorted(fdir.glob("*.json")) if fdir.exists() else []
    if not files:
        print("  (forward JSON なし)")
    else:
        latest = files[-1]
        data = json.loads(latest.read_text())
        ov = data.get("overall", {})
        meta = data.get("score_bucket_meta", {})
        print(f"  file: {latest.name}  decisions_n={data.get('decisions_n')}")
        print(f"  overall.current_mtm: {ov.get('current_mtm')}")
        print(f"  version_dist: {meta.get('version_dist')}")
        for b, v in (data.get("by_score_bucket") or {}).items():
            cm = v.get("current_mtm", {})
            print(f"    bucket {b}: current_mtm n={cm.get('n')} avg_excess={cm.get('avg_excess')}")

    print("\n" + _line("="))
    print("注: これは実ログ診断であり replay ではない。2週間窓は target60日 outcome に短く、")
    print("    勝率証明には不向き（pick傾向・偏り・発火・初期forwardの観測用）。")
    print(_line("="))


if __name__ == "__main__":
    main()
