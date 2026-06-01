"""異常検知 + HALT 機構（v2.10 Phase I-10）。

朝バッチで呼ばれる総合異常チェック:
  - ポートフォリオ DD（累計・日次）
  - 約定失敗の連続性（直近 N 営業日の Decision skipped/error 連続）
  - 価格異常（個別銘柄の前日比 ±X% は auto_fill 内で個別判定）

検知時の挙動:
  - auto モード: HALT ファイル発火 → 以降の auto 執行が全停止
  - manual モード: 警告ログのみ（人間判断のため停止しない）

HALT ファイル: ~/.trading-agent/HALT に JSON で書き出す。
既存 check_halt() は最初 200 文字を読むだけなので JSON でも互換維持。
解除は手動: `rm ~/.trading-agent/HALT` または clear_halt()。

ハルシネーション対策:
  - portfolio_snapshot が無い場合は判定不能 → 検知しない（推測しない）
  - 直近 1 件のみで判定しない（最低 2 件必要）
  - 失敗判定は Decision.status の事実のみ参照
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.anomaly_detector")

DEFAULT_HALT_FILE = Path("~/.trading-agent/HALT").expanduser()

# 既定閾値（環境変数で上書き可能）
DEFAULT_DD_THRESHOLD = -0.15  # 累計 -15% で HALT
DEFAULT_DAILY_DD_THRESHOLD = -0.07  # 単日 -7% で HALT
DEFAULT_CONSECUTIVE_FAIL_THRESHOLD = 3  # 約定失敗 3 連続で HALT
DEFAULT_PRICE_ANOMALY_THRESHOLD = 0.20  # 個別銘柄 ±20% は auto_fill でスキップ
DEFAULT_DD_BRAKE_THRESHOLD = -0.10  # 累計 DD -10% で新規 buy ブレーキ（H-7 用）


def _halt_file() -> Path:
    return DEFAULT_HALT_FILE


def get_halt_state() -> dict[str, Any]:
    """HALT 状態 + 理由 + 発火元 + 時刻を返す。

    既存の check_halt() は plain text も読めるので、JSON 書式とプレーンテキスト書式
    の両方を受け付ける（互換維持）。
    """
    path = _halt_file()
    if not path.exists():
        return {"halted": False}
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return {"halted": True, "reason": "(read failed)"}
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return {"halted": True, **data}
    except Exception:
        pass
    return {"halted": True, "reason": content.strip()[:200]}


def is_halted() -> bool:
    """HALT 状態の簡易チェック。"""
    return get_halt_state().get("halted", False)


def trigger_halt(reason: str, *, source: str) -> dict[str, Any]:
    """HALT 発火: 既存 HALT ファイルに JSON で reason/source/時刻を書き出す。

    既に HALT 中なら上書きせず現状維持（最初の発火元を保持）。
    """
    path = _halt_file()
    if path.exists():
        existing = get_halt_state()
        _log.warning(
            "halt_already_active",
            existing_reason=existing.get("reason"),
            new_reason=reason,
        )
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "reason": reason,
        "source": source,
        "triggered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.error("halt_triggered", reason=reason, source=source)

    # v2.10 P17: HALT 履歴を jsonl に追記
    try:
        history_path = path.parent / "HALT.history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as f:
            event = {"action": "triggered", "at": data["triggered_at"], "reason": reason, "source": source}
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("halt_history_write_failed", error_type=type(exc).__name__)

    # Phase I-11: HALT 発火を Discord 通知（fire-and-forget・失敗で本処理は止めない）
    try:
        from trading_agent.utils.discord_notifier import notify_discord

        notify_discord(
            f"🛑 **HALT 発火**\n理由: {reason}\n発火元: {source}",
            level="critical",
            title="WILLE HALT TRIGGERED",
            fields={"reason": reason, "source": source},
        )
    except Exception as exc:
        _log.warning("halt_discord_notify_failed", error_type=type(exc).__name__)

    return {"halted": True, **data}


def clear_halt() -> bool:
    """HALT 解除（手動操作）。解除成功で True、もともと無ければ False。"""
    path = _halt_file()
    if not path.exists():
        return False
    # 履歴用に解除前の理由を記憶
    prev_state = get_halt_state()
    path.unlink()
    _log.info("halt_cleared")

    # v2.10 P17: 解除イベントを履歴に追記
    try:
        history_path = path.parent / "HALT.history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as f:
            event = {
                "action": "cleared",
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "previous_reason": prev_state.get("reason", ""),
            }
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("halt_history_write_failed", error_type=type(exc).__name__)
    return True


def detect_portfolio_drawdown(
    engine: Engine,
    *,
    dd_threshold: float = DEFAULT_DD_THRESHOLD,
    daily_dd_threshold: float = DEFAULT_DAILY_DD_THRESHOLD,
    today: dt.date | None = None,
) -> dict[str, Any] | None:
    """portfolio_snapshot から DD を計算。閾値超なら detection 返す（None=正常）。

    判定:
      - 累計 DD: 直近 30 日の最大値から最新の下落率
      - 日次 DD: 最新スナップショットの daily_pnl_jpy / 前日 total_assets_jpy
    """
    if today is None:
        today = dt.date.today()
    cutoff = today - dt.timedelta(days=30)
    with Session(engine) as s:
        rows = list(
            s.exec(
                select(PortfolioSnapshot)
                .where(col(PortfolioSnapshot.date) >= cutoff)
                .order_by(col(PortfolioSnapshot.date))
            ).all()
        )
    if len(rows) < 2:
        return None  # 判定不能（推測しない）

    latest = rows[-1]
    prev = rows[-2]
    peak = max(r.total_assets_jpy for r in rows)
    cum_dd = (
        (latest.total_assets_jpy - peak) / peak
        if peak > 0
        else 0.0
    )
    daily_dd = (
        latest.daily_pnl_jpy / prev.total_assets_jpy
        if prev.total_assets_jpy > 0
        else 0.0
    )

    if cum_dd <= dd_threshold:
        return {
            "type": "cumulative_drawdown",
            "value": cum_dd,
            "threshold": dd_threshold,
            "peak": peak,
            "latest": latest.total_assets_jpy,
            "message": f"累計 DD {cum_dd:.1%} (閾値 {dd_threshold:.1%})",
        }
    if daily_dd <= daily_dd_threshold:
        return {
            "type": "daily_drawdown",
            "value": daily_dd,
            "threshold": daily_dd_threshold,
            "daily_pnl_jpy": latest.daily_pnl_jpy,
            "message": f"日次 DD {daily_dd:.1%} (閾値 {daily_dd_threshold:.1%})",
        }
    return None


def detect_consecutive_fill_failures(
    engine: Engine,
    *,
    broker_mode: str = "paper",
    threshold: int = DEFAULT_CONSECUTIVE_FAIL_THRESHOLD,
    today: dt.date | None = None,
) -> dict[str, Any] | None:
    """直近 N 営業日の Decision で fill 失敗（skipped）が連続したか判定。

    v2.10 致命候補 5 修正: **営業日ベース** で判定。
      - 朝バッチは平日のみ走るため、休場日には Decision が作られない
      - 「7 暦日」ではなく「7 営業日」で遡ることで連休前後の誤発火を防ぐ
      - Decision が無い営業日は連続にカウントしない

    Decision.status="skipped" が連続 threshold 件以上で異常。
    """
    if today is None:
        today = dt.date.today()
    # 営業日換算で 7 営業日 = 暦日換算で最大 14 日（連休考慮）
    # 余裕を持って 21 日遡る (3 連休 × 7 営業日のバッファ)
    cutoff = today - dt.timedelta(days=21)
    with Session(engine) as s:
        decisions = list(
            s.exec(
                select(Decision)
                .where(col(Decision.date) >= cutoff)
                .where(col(Decision.action) == "buy")
                .order_by(col(Decision.date).desc(), col(Decision.id).desc())
            ).all()
        )
    if not decisions:
        return None

    # 日付ごとに status を集約（同じ日に approved が 1 つでもあれば「失敗営業日」ではない）
    from collections import defaultdict

    by_date: dict[dt.date, set[str]] = defaultdict(set)
    for d in decisions:
        by_date[d.date].add(d.status)

    # 日付降順で連続失敗営業日をカウント
    consecutive = 0
    for date in sorted(by_date.keys(), reverse=True):
        statuses = by_date[date]
        # 「失敗営業日」= その日の Decision が全て skipped のみ
        if statuses == {"skipped"}:
            consecutive += 1
            if consecutive >= threshold:
                return {
                    "type": "consecutive_fill_failures",
                    "value": consecutive,
                    "threshold": threshold,
                    "latest_date": date.isoformat(),
                    "message": f"約定失敗 {consecutive} 営業日連続（直近 {date.isoformat()}）",
                }
        else:
            break  # approved が 1 つでもあれば連続途切れる
    return None


def is_price_anomaly(
    current_price: float,
    reference_price: float,
    threshold: float = DEFAULT_PRICE_ANOMALY_THRESHOLD,
) -> bool:
    """個別銘柄の前日比異常判定（auto_fill での個別スキップ用）。

    reference_price 0 以下や current_price 0 以下は判定不能 → False（推測しない）。
    """
    if reference_price <= 0 or current_price <= 0:
        return False
    deviation = abs(current_price - reference_price) / reference_price
    return deviation > threshold


def check_dd_brake(
    engine: Engine,
    *,
    threshold: float = DEFAULT_DD_BRAKE_THRESHOLD,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """ポートフォリオ累計 DD が閾値超なら新規 buy 抑制（v2.10 Phase H-7）。

    I-10 の HALT (≤-15%) より緩い閾値 (-10%) で、auto モードの日常 fill を抑制する。
    HALT との違い:
      - HALT: 全機能停止（buy/sell 両方）
      - DD ブレーキ: 新規 buy のみ抑制（保有は trailing/close_due で通常運用）

    Returns:
      {"brake_active": bool, "cum_dd": float | None, "threshold": float, "reason": str}
    """
    if today is None:
        today = dt.date.today()
    cutoff = today - dt.timedelta(days=30)
    with Session(engine) as s:
        rows = list(
            s.exec(
                select(PortfolioSnapshot)
                .where(col(PortfolioSnapshot.date) >= cutoff)
                .order_by(col(PortfolioSnapshot.date))
            ).all()
        )
    if len(rows) < 2:
        return {
            "brake_active": False,
            "cum_dd": None,
            "threshold": threshold,
            "reason": "insufficient_data",
        }
    peak = max(r.total_assets_jpy for r in rows)
    latest = rows[-1].total_assets_jpy
    cum_dd = (latest - peak) / peak if peak > 0 else 0.0
    if cum_dd <= threshold:
        result = {
            "brake_active": True,
            "cum_dd": round(cum_dd, 4),
            "threshold": threshold,
            "reason": f"累計 DD {cum_dd:.1%} ≤ 閾値 {threshold:.1%} → 新規 buy 抑制",
        }
        # Phase I-11: DD ブレーキ発火を Discord 通知（auto モード時のみ）
        try:
            from trading_agent.utils.discord_notifier import notify_discord
            from trading_agent.utils.lot_size import is_auto_mode

            if is_auto_mode():
                notify_discord(
                    f"⚠️ **DD ブレーキ発火**\n累計 DD: {cum_dd:.2%}\n閾値: {threshold:.2%}\n→ 新規 buy 抑制",
                    level="warning",
                    title="WILLE DD Brake",
                    fields={
                        "cum_dd": f"{cum_dd:.2%}",
                        "threshold": f"{threshold:.2%}",
                    },
                )
        except Exception as exc:
            _log.warning("dd_brake_discord_notify_failed", error_type=type(exc).__name__)
        return result
    return {
        "brake_active": False,
        "cum_dd": round(cum_dd, 4),
        "threshold": threshold,
        "reason": "ok",
    }


def run_anomaly_check(
    engine: Engine,
    *,
    broker_mode: str = "paper",
    today: dt.date | None = None,
) -> dict[str, Any]:
    """朝バッチで呼ばれる総合異常チェック。

    Returns:
      {
        "status": "already_halted" | "ok" | "halt_triggered" | "warning",
        "detections": [...],
        "halt_state": {...} | None,
      }

    挙動:
      - 既に HALT 中 → 何もせず返す
      - 検知あり + auto モード → HALT 発火
      - 検知あり + manual モード → 警告ログのみ（停止しない）
      - 検知なし → ok
    """
    from trading_agent.utils.lot_size import is_auto_mode

    if is_halted():
        return {
            "status": "already_halted",
            "detections": [],
            "halt_state": get_halt_state(),
        }

    detections: list[dict[str, Any]] = []
    dd = detect_portfolio_drawdown(engine, today=today)
    if dd:
        detections.append(dd)
    fails = detect_consecutive_fill_failures(
        engine, broker_mode=broker_mode, today=today
    )
    if fails:
        detections.append(fails)

    if not detections:
        return {"status": "ok", "detections": [], "halt_state": None}

    # manual モードは警告のみ（停止しない）
    if not is_auto_mode():
        for d in detections:
            _log.warning("anomaly_detected_manual_mode", **d)
        return {
            "status": "warning",
            "detections": detections,
            "halt_state": None,
        }

    # auto モードは HALT 発火
    reason = " | ".join(d["message"] for d in detections)
    state = trigger_halt(reason, source="anomaly_check")
    return {
        "status": "halt_triggered",
        "detections": detections,
        "halt_state": state,
    }
