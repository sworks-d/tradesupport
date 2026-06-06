"""Phase C ペーパーテストの現況を人間可読でまとめる（コスト0・DB のみ・LLM/価格 fetch なし）。

  .venv/bin/python scripts/phase_c_status.py            # 人間可読
  .venv/bin/python scripts/phase_c_status.py --json     # UI/ダッシュボード用 JSON

ユーザーが「テストの中身」を一目で掴むための統合ビュー。5 ブロック:
  ① 実行意図   いま何を回しているか（broker_mode / paper 解放枠 / 自動売買 / HALT）
  ② 損益       確定損益（closed Portfolio・broker_mode 別）。含み損益はダッシュボード（要価格）
  ③ 分析結果   ゲート⑥ paper/live、機体別 実績（n/命中/平均R）
  ④ 修正の方向 ゲート未通過要件＝何を改善すれば「勝てる」に近づくか / 昇格候補
  ⑤ 要素データ 母集団の内訳（status / filled_via / broker_mode / 公式集合 n）

build_phase_c_status(engine) は同じ内容を dict で返す（UI / snapshot が消費）。
価格 fetch を伴う含み損益・現在評価額は別途ダッシュボード（build_snapshot）で見る。
"""

from __future__ import annotations

import contextlib
import json as _json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any


@contextlib.contextmanager
def _suppressed():
    """build 中の構造化ログ(stdout)を抑止し stderr へ逃がす（--json/--archive を純 JSON に・codex P0）。

    structlog→stdlib→stdout 経路なので logging.disable(INFO) でログ自体を止め、
    併せて stdout を stderr へリダイレクト（二重防御）。終了後に必ず復帰。
    """
    logging.disable(logging.INFO)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        logging.disable(logging.NOTSET)

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.gate import (
    combined_gate_reference,
    official_gate_evaluation,
)
from trading_agent.evaluation.official_sources import OFFICIAL_FILL_SOURCES
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.misato import (
    auto_trade_view,
    check_halt,
    evaluate_promotions,
    treasury_view,
)
from trading_agent.reporting.feedback import (
    collect_feedback_records,
    compare_signal_tags_vs_baseline,
    summarize_feedback,
)


def _line(c: str = "─", n: int = 60) -> str:
    return c * n


# L3: 公式約定ソースは evaluation/official_sources に集約（paper_auto 除外理由もそこに明記）
_OFFICIAL_SOURCES = OFFICIAL_FILL_SOURCES


def _latest_forward_diagnosis() -> dict[str, Any]:
    """autoreport/forward/ の最新 forward 診断 JSON を **fetch せず** 読む（codex #4・read-only）。

    scripts/forward_diagnosis.py が yfinance で生成・archive したものを参照する（phase_c は価格 fetch しない）。
    未生成・読込失敗は空（推測しない）。
    """
    d = Path("autoreport/forward")
    if not d.exists():
        return {}
    files = sorted(d.glob("*.json"))
    if not files:
        return {}
    try:
        return _json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}


def _purchase_history(engine, *, broker_mode: str = "paper") -> dict[str, Any]:
    """約定履歴（何を・いつ・いくらで・何株 買ったか）= トレード台帳（read-only・価格 fetch なし）。

    official（紐付く Decision の filled_via∈ds_dispatch/manual）の paper 約定を時系列（新しい順）で返す。
    closed は売値/理由/損益も併記。legacy（cleanup/reset/分割/上場廃止由来）は件数のみ別掲（codex の
    official/legacy 分離指摘）。含み損益はダッシュボード側（価格 fetch が要るためここでは出さない）。
    """
    with Session(engine) as s:
        ports = s.exec(
            select(Portfolio).where(col(Portfolio.broker_mode) == broker_mode)
        ).all()
        decs = {d.id: d for d in s.exec(select(Decision)).all()}
        names = {u.ticker: u.name for u in s.exec(select(Universe)).all()}

    official: list[dict[str, Any]] = []
    legacy_n = 0
    for p in ports:
        d = decs.get(getattr(p, "decision_id", None))
        is_official = d is not None and d.filled_via in _OFFICIAL_SOURCES
        if not is_official:
            legacy_n += 1
            continue
        qty = float(p.qty or 0)
        buy = float(p.buy_price or 0)
        closed = p.status == "closed"
        sell = float(p.closed_price or 0) if closed else None
        pnl = round((sell - buy) * qty) if (closed and sell) else None
        official.append({
            "ticker": p.ticker,
            "name": names.get(p.ticker, ""),
            "buy_date": p.buy_date.isoformat() if p.buy_date else None,
            "buy_price": round(buy, 2),
            "qty": qty,
            "cost_jpy": round(buy * qty),          # 取得原価（何をいくらで×何株）
            "status": p.status,                     # active / closed
            "sell_price": round(sell, 2) if sell else None,
            "closed_reason": p.closed_reason if closed else None,
            "realized_pnl_jpy": pnl,                # closed のみ（active は含み=ダッシュボード）
            "personality": p.personality,
            "filled_via": d.filled_via,
            "entry_signal_tags": list(d.entry_signal_tags or []),
            "entry_exposure_recommendation": d.entry_exposure_recommendation,
        })
    # 新しい買い順（buy_date 降順・None は末尾）
    official.sort(key=lambda r: (r["buy_date"] or ""), reverse=True)
    return {
        "broker_mode": broker_mode,
        "purchases": official,
        "official_n": len(official),
        "legacy_excluded_n": legacy_n,
        "note": (
            "official(ds_dispatch/manual)の約定のみ。legacy(cleanup/reset/分割/上場廃止由来)は件数のみ別掲。"
            "active の含み損益は価格 fetch が要るためダッシュボード側で表示。"
        ),
    }


def _realized_pnl(engine) -> dict[str, dict]:
    """closed Portfolio の確定損益を broker_mode 別 × official/legacy で集計（gross・コスト0）。

    codex 指摘: cleanup/misato_reset/fractional/delisted 由来の legacy closed を「確定取引」と
    混ぜると、Phase C 開始直後なのに大量取引済みに見えて gate n=0 と矛盾＝誤読。
    公式（紐付く Decision が filled_via∈(ds_dispatch,manual) ∧ entry_broker_mode 一致）と
    legacy（参考・cleanup 等）を分離する。
    """
    with Session(engine) as s:
        rows = s.exec(select(Portfolio).where(col(Portfolio.status) == "closed")).all()
        decs = {d.id: d for d in s.exec(select(Decision)).all()}

    def _blank() -> dict:
        # codex #1: 旧 UI(LiveData.tsx) は pnl_realized[mode].{n,pnl_jpy,wins} を読むため、
        # 後方互換でフラットキー（= official 値）を併存させる。新 UI は official/legacy_reference を使う。
        return {"n": 0, "pnl_jpy": 0.0, "wins": 0,
                "official": {"n": 0, "pnl_jpy": 0.0, "wins": 0},
                "legacy_reference": {"n": 0, "pnl_jpy": 0.0}}

    out: dict[str, dict] = {}
    for p in rows:
        mode = p.broker_mode or "unknown"
        bucket = out.setdefault(mode, _blank())
        if p.closed_price is None or not p.buy_price:
            continue
        pnl = (float(p.closed_price) - float(p.buy_price)) * float(p.qty or 0)
        d = decs.get(getattr(p, "decision_id", None))
        is_official = bool(
            d is not None
            and d.filled_via in _OFFICIAL_SOURCES
            and d.entry_broker_mode == mode
        )
        if is_official:
            bucket["official"]["n"] += 1
            bucket["official"]["pnl_jpy"] += pnl
            if pnl > 0:
                bucket["official"]["wins"] += 1
        else:
            bucket["legacy_reference"]["n"] += 1
            bucket["legacy_reference"]["pnl_jpy"] += pnl
    # 後方互換フラットキー = official（旧 UI が「確定取引」として読む値。legacy は混ぜない）
    for b in out.values():
        b["n"] = b["official"]["n"]
        b["pnl_jpy"] = b["official"]["pnl_jpy"]
        b["wins"] = b["official"]["wins"]
    return out


def _decision_breakdown(engine) -> dict:
    with Session(engine) as s:
        decs = s.exec(select(Decision)).all()
    return {
        "total": len(decs),
        "by_status": Counter(d.status for d in decs),
        "by_filled_via": Counter(d.filled_via for d in decs),
        "by_broker_mode": Counter(d.entry_broker_mode for d in decs),
        "evaluated": sum(1 for d in decs if d.hit_or_miss in ("hit", "miss", "neutral")),
    }


def _signal_tag_firing(engine) -> dict[str, Any]:
    """発火率の可視化（codex・欺瞞防止）: verified 済 decision に signal_tag が何件立ったか。

    edge器(signal_tag_vs_baseline)は「タグ付き decision の成績」を測るが、その手前の
    「そもそもタグが何件立っているか（＝発火率）」が見えないと、空シグナルが silently empty になる。
    news 系（headline 辞書由来・補助）と構造化 earnings 系（J-Quants 由来・主）の発火率を、
    verified decision 母数に対して出す。DB のみ・コスト0・売買不変（record-only タグの観測）。

    発火率が 0 のまま続く＝辞書/開示接続が機能していない兆候（「動いてる風」を防ぐ）。
    """
    with Session(engine) as s:
        decs = s.exec(select(Decision)).all()
    verified = [d for d in decs if d.verified_at is not None]
    n = len(verified)

    def _rate(hits: int) -> float:
        return round(100.0 * hits / n, 1) if n else 0.0

    def _count(tag: str) -> int:
        return sum(1 for d in verified if tag in (d.entry_signal_tags or []))

    # 1) tag 種別ごとの発火数（一括でなく種別分解・M1 観測 hardening）
    struct_tags = [
        "event_upward_revision", "event_downward_revision",
        "event_dividend_hike", "event_dividend_cut", "earnings_accel",
    ]
    news_tags = ["news_positive", "news_negative"]
    by_tag = {t: _count(t) for t in (struct_tags + news_tags)}

    any_news = sum(
        1 for d in verified
        if any(str(t).startswith("news_") for t in (d.entry_signal_tags or []))
    )
    any_struct = sum(
        1 for d in verified
        if any(str(t).startswith(("earnings_", "event_")) for t in (d.entry_signal_tags or []))
    )

    # 2) no-fire 理由 breakdown（signal_tag_sources["_event_diag"] 集計）。
    # 『なぜ構造化イベントが発火しないか』(fin_not_fetched=rate-limit / no_change /
    # single_fy_point / missing_shares / split_suspected / no_prior_fy_data 等)を理由別カウント。
    forecast_reasons: Counter = Counter()
    dividend_reasons: Counter = Counter()
    diag_present = 0
    for d in verified:
        src = d.signal_tag_sources or {}
        diag = src.get("_event_diag") if isinstance(src, dict) else None
        if isinstance(diag, dict):
            diag_present += 1
            forecast_reasons[str(diag.get("forecast", "unknown"))] += 1
            dividend_reasons[str(diag.get("dividend", "unknown"))] += 1

    return {
        "verified_decisions": n,
        "by_tag": by_tag,  # tag 種別ごとの発火数
        "news": {
            "positive": by_tag["news_positive"],
            "negative": by_tag["news_negative"],
            "no_tag": n - any_news,
            "fire_rate_pct": _rate(any_news),
        },
        "structured": {
            "upward_revision": by_tag["event_upward_revision"],
            "downward_revision": by_tag["event_downward_revision"],
            "dividend_hike": by_tag["event_dividend_hike"],
            "dividend_cut": by_tag["event_dividend_cut"],
            "earnings_accel": by_tag["earnings_accel"],
            "fire_rate_pct": _rate(any_struct),
        },
        # M1 観測 hardening: 構造化イベントの no-fire 理由（なぜ発火しないかを一目で・空の罠可視化）
        "no_fire_reasons": {
            "diag_recorded": diag_present,        # _event_diag が記録された verified 数
            "forecast": dict(forecast_reasons),   # fin_not_fetched / no_change / single_fy_point …
            "dividend": dict(dividend_reasons),   # missing_shares / split_suspected / no_prior_fy_data …
        },
        "note": (
            "発火率0が続く=接続/データが機能していない（空シグナル検知・欺瞞防止）。news(辞書)は補助、"
            "構造化イベント(J-Quants由来)が主。no_fire_reasons で『なぜ発火しないか』(rate-limit=fin_not_fetched"
            "/データ欠損/抑止)を観測する。"
        ),
    }


def _print_intent(engine) -> None:
    print(_line("="))
    print("① 実行意図（いま何を回しているか）")
    print(_line("="))
    for mode in ("paper", "live"):
        v = treasury_view(engine, mode)
        if v.get("target_ceiling_jpy"):
            print(f"[{mode}] 口座総額 ¥{v['account_capital_jpy']:,}（固定）")
            print(f"      解放済み deploy 上限 ¥{v['current_risk_budget_jpy']:,} / "
                  f"上限 ¥{v['target_ceiling_jpy']:,}（{v['ceiling_progress_pct']}% 解放）")
            print(f"      使用中 exposure ¥{v['active_exposure_jpy']:,} → "
                  f"今 deploy 可能 ¥{v.get('deployable_jpy') or 0:,}")
        else:
            print(f"[{mode}] 預かり ¥{v['seed_jpy']:,} / 配分 ¥{v['allocated_jpy']:,} "
                  f"/ 未配分 ¥{v['available_jpy']:,}（Phase C 未初期化）")
    av = auto_trade_view(engine)
    m = av["master"]
    on = [k for k, st in av["per_pilot"].items() if st["active"]]
    print(f"自動売買: master {'🟢ON' if m['active'] else '⚫OFF'} / 稼働機 {on or 'なし'}")
    halted, reason = check_halt()
    print(f"HALT: {'⛔ ON ' + (reason or '') if halted else '🟢 なし'}")


def _print_pnl(engine) -> None:
    print()
    print(_line("="))
    print("② 損益（確定・gross／含み損益はダッシュボード）")
    print(_line("="))
    pnl = _realized_pnl(engine)
    any_official = any(v["official"]["n"] for v in pnl.values())
    if not any_official:
        print("公式 Phase C 確定取引まだなし（Phase C 開始後に蓄積・gate n=0 と整合）")
    for mode, d in pnl.items():
        o = d["official"]
        if o["n"] > 0:
            wr = o["wins"] / o["n"] * 100
            sign = "+" if o["pnl_jpy"] >= 0 else ""
            print(f"[{mode}] 公式 確定 {o['n']}件 / 勝ち {o['wins']}（{wr:.0f}%）/ "
                  f"確定損益 {sign}¥{o['pnl_jpy']:,.0f}")
        lg = d["legacy_reference"]
        if lg["n"] > 0:
            print(f"[{mode}] （参考）legacy closed {lg['n']}件（cleanup/reset 等・公式対象外・"
                  f"gross ¥{lg['pnl_jpy']:,.0f}）")


def _print_analysis(engine) -> None:
    print()
    print(_line("="))
    print("③ 分析結果（ゲート⑥ + 機体別実績）")
    print(_line("="))
    for mode in ("paper", "live"):
        print(official_gate_evaluation(engine, broker_mode=mode).summary())
        print()
    print(combined_gate_reference(engine).summary())
    print()
    print("--- 機体別 実績（paper）---")
    perf = summarize_feedback(collect_feedback_records(engine, broker_mode="paper")).get(
        "by_personality", {}
    )
    if not perf:
        print("  まだ評価データなし")
    for name, d in perf.items():
        print(f"  {name or '—':8s} n={int(d.get('n',0))} "
              f"命中={float(d.get('hit_rate',0))*100:.0f}% 平均R={float(d.get('avg_r',0)):+.2f}")


def _print_fix_direction(engine) -> None:
    print()
    print(_line("="))
    print("④ 修正の方向性（何を改善すれば『勝てる』に近づくか）")
    print(_line("="))
    gate = official_gate_evaluation(engine, broker_mode="paper")
    failing = [c for c in gate.criteria if not c.passed]
    if not failing:
        print("paper ゲート⑥ 全要件 達成 → 増額余地あり（--advance-paper）")
    else:
        print("paper ゲート⑥ 未達要件 → 改善対象:")
        for c in failing:
            print(f"  ✗ {c.name}: 現在 {c.value} / 要件 {c.threshold}")
    proms = evaluate_promotions(engine, broker_mode="paper")
    if proms:
        print("--- 昇格候補（paper）---")
        for p in proms:
            print(f"  {p.personality}: {p.note}")


def _print_data(engine) -> None:
    print()
    print(_line("="))
    print("⑤ 要素データ（母集団の内訳）")
    print(_line("="))
    b = _decision_breakdown(engine)

    def _relabel(c: Counter) -> dict:
        # None は legacy/公式対象外（そのまま None と出すと壊れて見える・codex 指摘3）
        return {("legacy(None)" if k is None else k): v for k, v in c.items()}

    print(f"decisions 総数 {b['total']} / 評価済 {b['evaluated']}")
    print(f"  status:      {_relabel(b['by_status'])}")
    print(f"  filled_via:  {_relabel(b['by_filled_via'])}")
    print(f"  broker_mode: {_relabel(b['by_broker_mode'])}")
    print("※ 公式集合（ゲート⑥対象）= filled_via∈(ds_dispatch,manual) ∧ entry_market_regime 有 ∧ broker_mode 一致")

    f = _signal_tag_firing(engine)
    nws, st = f["news"], f["structured"]
    _empty = f["verified_decisions"] and st["fire_rate_pct"] == 0
    fire_flag = "🔴 空シグナル疑い" if _empty else "🟢"
    print()
    print(f"signal_tag 発火（verified {f['verified_decisions']}件中）{fire_flag}")
    print(f"  news（補助/辞書）: +{nws['positive']} / -{nws['negative']} / 無タグ{nws['no_tag']}"
          f"（発火 {nws['fire_rate_pct']}%）")
    print(f"  構造化（主/J-Quants）: 上方修正{st['upward_revision']} / 下方修正"
          f"{st['downward_revision']} / 増配{st['dividend_hike']} / 減配{st['dividend_cut']}"
          f" / earnings_accel{st['earnings_accel']}（発火 {st['fire_rate_pct']}%）")
    nfr = f["no_fire_reasons"]
    if nfr["diag_recorded"]:
        print(f"  no-fire 理由（diag {nfr['diag_recorded']}件）forecast={nfr['forecast']} / "
              f"dividend={nfr['dividend']}")


def _print_v2(engine) -> None:
    """Review Report v2: 配分透明性 + exit/stance/局面別 + Decision 明細件数（コスト0）。"""
    from trading_agent.portfolio.feedback import compute_pilot_multipliers

    print()
    print(_line("="))
    print("⑥ フィードバック透明性（なぜこの機体に予算が寄るか・paper）")
    print(_line("="))
    pm = compute_pilot_multipliers(engine, broker_mode="paper")
    print(f"status={pm.get('status')} / lookback={pm.get('lookback_days')}日")
    for pilot, d in pm.get("details", {}).items():
        print(f"  {pilot:8s} x{d.get('multiplier')} "
              f"(精度={d.get('accuracy_pct')} 評価={d.get('evaluated')}件) {d.get('reason','')}")

    recs = collect_feedback_records(engine, broker_mode="paper", official_only=True)
    bd = _breakdowns(recs)
    opens = _open_positions_paper(engine)
    print()
    print(_line("="))
    print("⑦ 成績パターン（exit理由別 / stance別 / 局面別・paper 公式）")
    print(_line("="))
    if not recs:
        print("まだ評価データなし（Phase C 蓄積後に勝ち/負けパターンが見える）")
    else:
        for label, key in (("exit理由", "by_exit_reason"), ("stance", "by_stance"), ("局面", "by_regime")):
            rows = {k: v for k, v in bd[key].items() if v["n"] > 0}
            if rows:
                print(f"[{label}別] " + " / ".join(
                    f"{k}: n={v['n']} 命中{(v['hit_rate'] or 0)*100:.0f}% R{(v['avg_r'] or 0):+.2f}"
                    for k, v in rows.items()))
    _dec_n = len({r["decision_id"] for r in recs})
    print(f"\n→ 公式 paper 明細: Decision {_dec_n} 件 / fill record {len(recs)} 件"
          f"（gate n は Decision 粒度・明細/breakdowns は fill record 粒度）/ 評価前の保有: {len(opens)} 件"
          "（JSON: decisions_detail_paper / open_positions_paper に全項目）")

    sb = _size_breakdown_paper(engine)
    bb = sb["by_bucket"]
    flag = "🟢 大指針 OK" if sb["policy_ok"] else "🔴 大型偏重(大指針逸脱)"
    print()
    print(_line("="))
    print(f"⑧ 保有の size 分布（大指針 #2: 中小型成長株）— {flag}")
    print(_line("="))
    print(f"  小型 {bb['small']['n']} / 中型 {bb['mid']['n']} / 大型 {bb['large']['n']} / 不明 {bb['unknown']['n']}")
    print(f"  (大型+不明) 件数比 {sb['risky_n_pct']}% / 取得原価比 {sb['risky_cost_pct']}%"
          "（どちらか 40%超で逸脱・不明は検証不能で要注意）")


def _gate_dict(gate) -> dict[str, Any]:
    return {
        "broker_mode": gate.broker_mode,
        "passed": gate.passed,
        "actionable": gate.actionable,
        "n": gate.n,
        "criteria": [
            {"name": c.name, "value": str(c.value), "threshold": c.threshold,
             "passed": c.passed}
            for c in gate.criteria
        ],
        "notes": gate.notes,
    }


def _size_breakdown_paper(engine) -> dict[str, Any]:
    """大指針 #2 の再発検知: active paper 保有を size_bucket(small/mid/large)別に集計（コスト0）。

    Universe.market_cap_jpy から size_bucket を判定し、件数・取得コストを出す。
    large の比率が高い＝中小型成長戦略から逸脱（2026-06-04 の大型偏重を即可視化するため）。
    """
    from trading_agent.models.universe import Universe
    from trading_agent.wille.opportunity_fill import size_bucket

    with Session(engine) as s:
        ports = s.exec(
            select(Portfolio)
            .where(col(Portfolio.status) == "active")
            .where(col(Portfolio.broker_mode) == "paper")
        ).all()
        mc = {
            u.ticker: u.market_cap_jpy
            for u in s.exec(select(Universe)).all()
        }
    out: dict[str, dict] = {
        b: {"n": 0, "cost_jpy": 0.0} for b in ("small", "mid", "large", "unknown")
    }
    for p in ports:
        bucket = size_bucket(mc.get(p.ticker))
        cost = float(p.buy_price or 0) * float(p.qty or 0)
        out[bucket]["n"] += 1
        out[bucket]["cost_jpy"] += cost
    total_n = sum(b["n"] for b in out.values())
    total_cost = sum(b["cost_jpy"] for b in out.values())
    # codex P1: 件数比だけでなく取得原価比でも判定。unknown(時価総額不明)は検証不能なので
    # 大型と同様に「中小型でない」リスク扱いにする（lookup 漏れ等で大型がすり抜けるのを防ぐ）。
    risky_n = out["large"]["n"] + out["unknown"]["n"]
    risky_cost = out["large"]["cost_jpy"] + out["unknown"]["cost_jpy"]
    large_pct = (out["large"]["n"] / total_n * 100) if total_n else 0.0
    risky_n_pct = (risky_n / total_n * 100) if total_n else 0.0
    risky_cost_pct = (risky_cost / total_cost * 100) if total_cost else 0.0
    return {
        "by_bucket": out,
        "total_n": total_n,
        "large_pct": round(large_pct, 1),
        "risky_n_pct": round(risky_n_pct, 1),       # (大型+不明) 件数比
        "risky_cost_pct": round(risky_cost_pct, 1),  # (大型+不明) 取得原価比
        # 大指針: 大型+不明が 件数・原価 どちらでも 40% 超なら逸脱。unknown は検証不能で要注意。
        "policy_ok": risky_n_pct <= 40.0 and risky_cost_pct <= 40.0,
    }


def _open_positions_paper(engine) -> list[dict[str, Any]]:
    """評価前の active paper 保有スナップショット（何を・なぜ・いつ評価予定・現状態）。

    codex P1/P2「余すことなく記録」: 評価済み明細だけだと途中の保有意図/評価予定が追えない。
    価格 fetch なし（含み損益はダッシュボード側）。Portfolio active + 紐付く Decision から構成。
    """
    with Session(engine) as s:
        ports = s.exec(
            select(Portfolio)
            .where(col(Portfolio.status) == "active")
            .where(col(Portfolio.broker_mode) == "paper")
        ).all()
        decs = {d.id: d for d in s.exec(select(Decision)).all()}
    out: list[dict[str, Any]] = []
    for p in ports:
        d = decs.get(getattr(p, "decision_id", None))
        qty = float(p.qty or 0)
        buy = float(p.buy_price or 0)
        out.append({
            # トレース用 ID（後で損益・分析・修正方向を追える・codex P2）
            "portfolio_id": p.id,
            "decision_id": getattr(p, "decision_id", None),
            "broker_mode": p.broker_mode,
            "status": p.status,
            "ticker": p.ticker,
            "qty": qty,
            "buy_price": buy,
            "cost_jpy": round(buy * qty),  # 取得原価（含み損益はダッシュボード側＝要価格）
            "buy_date": p.buy_date.isoformat() if p.buy_date else None,
            "target_date": p.target_date.isoformat() if p.target_date else None,
            "stop_loss_pct": float(p.stop_loss_pct or 0),
            "target_pct": float(getattr(p, "target_pct", 0) or 0),
            "strategy_category": getattr(p, "strategy_category", None),
            "personality": p.personality,
            "planned_total_qty": getattr(p, "planned_total_qty", None),
            # 紐付く Decision の意図/評価予定/経路（価格 fetch なし）
            "filled_via": getattr(d, "filled_via", None) if d else None,
            "entry_broker_mode": getattr(d, "entry_broker_mode", None) if d else None,
            "entry_market_regime": getattr(d, "entry_market_regime", None) if d else None,
            "evaluation_date": (
                d.evaluation_date.isoformat() if d and d.evaluation_date else None
            ),
            "target_period_days": getattr(d, "target_period_days", None) if d else None,
            "gendo_stance": getattr(d, "gendo_stance", None) if d else None,
            "thesis": (getattr(d, "thesis_at_decision", "") or "")[:120] if d else "",
        })
    return out


def _breakdowns(records: list[dict[str, Any]]) -> dict[str, dict]:
    """Review Report v2: exit理由別 / stance別 / 局面別 の n・命中・平均R（broker_mode 絞り済み records）。"""
    def _group(key: str) -> dict[str, dict]:
        g: dict[str, dict] = {}
        for r in records:
            k = str(r.get(key) or "—")
            d = g.setdefault(k, {"n": 0, "hits": 0, "r_sum": 0.0})
            if r.get("hit_or_miss") in ("hit", "miss", "neutral"):
                d["n"] += 1
                if r.get("hit_or_miss") == "hit":
                    d["hits"] += 1
                if r.get("r_multiple") is not None:
                    d["r_sum"] += float(r["r_multiple"])
        return {
            k: {"n": v["n"],
                "hit_rate": (v["hits"] / v["n"]) if v["n"] else None,
                "avg_r": (v["r_sum"] / v["n"]) if v["n"] else None}
            for k, v in g.items()
        }
    return {
        "by_exit_reason": _group("exit_reason"),
        "by_stance": _group("gendo_stance"),
        "by_regime": _group("entry_market_regime"),
    }


def build_phase_c_status(engine) -> dict[str, Any]:
    """Phase C 現況を構造化 dict で返す（UI / snapshot 用・コスト0・価格 fetch なし）。"""
    from trading_agent.portfolio.feedback import compute_pilot_multipliers

    paper_gate = official_gate_evaluation(engine, broker_mode="paper")
    live_gate = official_gate_evaluation(engine, broker_mode="live")
    combined = combined_gate_reference(engine)
    av = auto_trade_view(engine)
    halted, reason = check_halt()
    # 機体別実績は paper 全体（昇格集計と整合）、Review Report v2 の「公式」明細は official_only。
    paper_records = collect_feedback_records(engine, broker_mode="paper")
    paper_official = collect_feedback_records(
        engine, broker_mode="paper", official_only=True
    )
    _paper_summary = summarize_feedback(paper_records)
    perf = _paper_summary.get("by_personality", {})
    sig_tag_perf = _paper_summary.get("by_signal_tags", {})
    exposure_perf = _paper_summary.get("by_exposure", {})  # Track A: マクロ posture 別成績
    sig_tag_vs_baseline = compare_signal_tags_vs_baseline(paper_records)  # codex #3: 正味エッジ
    proms = evaluate_promotions(engine, broker_mode="paper")
    # 配分の透明性（なぜこの機体に予算が寄るか）= pilot_multipliers の根拠（broker_mode=paper）
    pilot_mult = compute_pilot_multipliers(engine, broker_mode="paper")
    return {
        "intent": {
            "treasury": {m: treasury_view(engine, m) for m in ("paper", "live")},
            "auto_trade": {
                "master_on": av["master"]["active"],
                "pilots_on": [k for k, st in av["per_pilot"].items() if st["active"]],
            },
            "halt": {"on": halted, "reason": reason},
        },
        "pnl_realized": _realized_pnl(engine),
        "gates": {
            "paper": _gate_dict(paper_gate),
            "live": _gate_dict(live_gate),
            "combined_reference": _gate_dict(combined),
        },
        "pilot_performance_paper": {
            (name or "—"): {
                "n": int(d.get("n", 0)),
                "hit_rate": float(d.get("hit_rate", 0.0)),
                "avg_r": float(d.get("avg_r", 0.0)),
                "broker_mode": "paper",
            }
            for name, d in perf.items()
        },
        # Track B: signal_tag 別の shadow 成績（sector_rs/pead 等）。**売買は変えていない**＝
        # record-only 観測。tag 別 n>=20 で null 比較を継続的に上回ったものだけ将来 score 加点に昇格。
        "signal_tag_performance_paper": {
            "tags": {
                tag: {
                    "n": int(d.get("n", 0)),
                    "hit_rate": float(d.get("hit_rate", 0.0)),
                    "avg_r": float(d.get("avg_r", 0.0)),
                    "hit": int(d.get("hit", 0)),
                    "miss": int(d.get("miss", 0)),
                    "verdict": (
                        "判定可" if int(d.get("n", 0)) >= 20 else "サンプル不足(n<20)"
                    ),
                }
                for tag, d in sig_tag_perf.items()
            },
            "note": "record-only（売買未変更）。tag 別 n>=20 で残す/落とすを判断。空=タグ付き fill 未到達。",
        },
        # codex #3: tag 有無の対照成績（正味エッジ）。naive な hit_rate のバイアスを補正。
        # with(タグあり) vs without(タグなし) の hit_rate/avg_r 差 = net。同 filled universe 内比較。
        "signal_tag_vs_baseline_paper": sig_tag_vs_baseline,
        # Track A: exposure recommendation 別成績（record-only）。マクロ posture が結果と相関するか。
        # **sizing は未変更**＝今は記録のみ。null を超えたら将来 sizing/閾値の小幅調整へ昇格。
        "exposure_performance_paper": exposure_perf,
        # codex #4: forward 早期診断（評価期日60日を待たず 5/20/40/60日 対TOPIX超過）。
        # scripts/forward_diagnosis.py が autoreport/forward/ に archive したものを **fetch せず** 読む。
        # gate を前倒しで通すためでなく、観測空白(6月約定→8月評価)の仮説棄却用。空=未生成。
        "forward_diagnosis_paper": _latest_forward_diagnosis(),
        # 約定履歴（何を・いつ・いくらで・何株 買ったか）= トレード台帳（read-only・fetch なし）。
        "purchase_history_paper": _purchase_history(engine, broker_mode="paper"),
        "fix_direction_paper": {
            "failing_criteria": [
                {"name": c.name, "value": str(c.value), "threshold": c.threshold}
                for c in paper_gate.criteria if not c.passed
            ],
            "promotions": [
                {"personality": p.personality, "n": p.n, "hit_rate": p.hit_rate,
                 "avg_r": p.avg_r, "note": p.note}
                for p in proms
            ],
        },
        "data_breakdown": {
            k: (dict(v) if isinstance(v, Counter) else v)
            for k, v in _decision_breakdown(engine).items()
        },
        # 発火率の可視化（codex・欺瞞防止）: signal_tag が verified decision に何件立っているか。
        # edge器(下の signal_tag_vs_baseline)の手前で「そもそもタグが立っているか」を見せ、
        # 空シグナルが silently empty になるのを防ぐ。news=補助 / 構造化イベント=主。
        "signal_tag_firing": _signal_tag_firing(engine),
        # === Review Report v2（codex 仕様）===
        # 粒度の明示（codex P1/P2）: gate n は Decision 粒度、明細/breakdowns は fill record 粒度
        # （1 Decision を複数機体が fill すると record が増える）。UI は両者を区別して見せる。
        "official_decision_n": len({r["decision_id"] for r in paper_official}),
        "official_fill_record_n": len(paper_official),
        # section2: 公式 paper の Decision 明細（fill record 粒度・gate 公式述語と同条件）
        "decisions_detail_paper": paper_official,
        # 評価前の保有スナップショット（余すことなく記録・codex P1/P2）。価格 fetch なし。
        "open_positions_paper": _open_positions_paper(engine),
        # M5(大指針 #2): 保有の size_bucket 内訳（大型偏重の再発を即検知）。
        "size_breakdown_paper": _size_breakdown_paper(engine),
        # exit理由別 / stance別 / 局面別 の成績（公式 paper・勝ち負けパターンの手掛かり）
        "breakdowns_paper": _breakdowns(paper_official),
        # 配分の透明性: なぜこの機体に予算が寄るか（pilot_multipliers の根拠）
        "feedback_transparency": {
            "status": pilot_mult.get("status"),
            "lookback_days": pilot_mult.get("lookback_days"),
            "pilot_details": pilot_mult.get("details", {}),  # multiplier/accuracy/evaluated/reason
            "note": "データ不足時は全機 multiplier=1.0（中立）。broker_mode=paper の実績のみ。",
        },
        "notes": "BT 結果は退行/事故検出用で自動配分には還元しない。含み損益はダッシュボード（build_snapshot）で。",
    }


def _json_safe(data: dict[str, Any]) -> dict[str, Any]:
    """data_breakdown の None キーを文字列化（JSON 化 + 誤読防止・codex 指摘3）。"""
    bd = data.get("data_breakdown", {})
    for key in ("by_filled_via", "by_broker_mode", "by_status"):
        if isinstance(bd.get(key), dict):
            bd[key] = {
                ("legacy(None)" if k is None else str(k)): v for k, v in bd[key].items()
            }
    return data


def archive_to_file(engine, on_date: str) -> Path:
    """その日の Phase C 現況 JSON を autoreport/phase_c/YYYY-MM-DD.json に保存（履歴を余さず残す）。

    on_date は呼び出し側から渡す（スクリプト内で Date.now を使わない方針）。冪等（同日上書き）。
    """
    out_dir = Path(__file__).resolve().parent.parent / "autoreport" / "phase_c"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{on_date}.json"
    with _suppressed():
        data = _json_safe(build_phase_c_status(engine))
    data["archived_for_date"] = on_date
    out.write_text(_json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    if "--archive" in sys.argv:
        from trading_agent.utils.time_utils import today_jst

        path = archive_to_file(engine, today_jst().isoformat())
        print(f"✓ Phase C 現況を archive: {path}")
        return
    if "--json" in sys.argv:
        with _suppressed():
            data = _json_safe(build_phase_c_status(engine))
        print(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    print("\n■ INVESTIGELION Phase C ペーパーテスト 現況（コスト0・DB のみ）\n")
    _print_intent(engine)
    _print_pnl(engine)
    _print_analysis(engine)
    _print_fix_direction(engine)
    _print_v2(engine)
    _print_data(engine)
    print()
    print("→ 含み損益・現在評価額はダッシュボード（build_snapshot）で。BT 結果は退行/事故検出用で自動配分には還元しない。")


if __name__ == "__main__":
    main()
