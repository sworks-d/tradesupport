"""zeele_llm_scout（ZEELE LLM 探索エージェント・Phase 4-B）。

設計意図 (architecture.html / SYSTEM_PURPOSE.md):
  ZEELE = LLM Haiku で V字 / テーマ / 攻め銘柄を探索する車線。

役割（zeele_curator との分担）:
  - zeele_curator (既存・決定論): 3 週連続 screening 入賞銘柄を保守的に upsert
  - zeele_llm_scout (本モジュール・Phase 4-B): LLM で「screening 漏れ」の
    V字/テーマ/攻め候補を発掘し、ZeeleState に preset 指定で追加

入力:
  - universe.is_active=True で screening 候補外の銘柄
  - 各銘柄の直近 ScreeningResult（あれば）+ TickerBrief 5 中立スコア（あれば）

LLM 探索:
  - Haiku で銘柄ごとに preset を判定（7 種類 enum）
  - confidence + 短い reason（80 字）を JSON で
  - cost 制御: D1 keyword filter は不要（universe 全体を対象とするため別ロジック）
  - cost 制御: D2 入力短縮、D3 JSON schema 固定、D5 24h cache、BudgetGuard

ハルシネーション対策 (H2 値域検証):
  - preset enum 強制: 想定外なら "alpha" フォールバック
  - confidence 値域: 0.0-1.0 外なら 0.0
  - ticker 照合 (C2): universe 外なら捨てる
  - プロンプト: 「universe 以外の銘柄を返さない / 推測でデータ作らない」

安全装置:
  - 構築期間中は既定で無効（Setting zeele_llm_scout_enabled=true で明示有効化）。
    朝バッチからの無断自動課金を防ぐ（A-3）。
  - 1 バッチ最大 _MAX_LLM_CALLS_PER_BATCH 件まで（既定 30）
  - per-agent 予算: daily_budget_jpy / monthly_budget_jpy（既定 ¥10 / ¥200）を
    cost_logs(agent=zeele_llm_scout) で実集計して上限適用（A-3：旧 dead param を実装）。
    併せて全体 BudgetGuard（共有 ¥500/¥5000）でも二重ガード。
  - LLM 失敗時は zeele_curator の決定論結果のみで進む（後方互換）
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.llm.budget import BudgetGuard
from trading_agent.models.analytics import CostLog
from trading_agent.models.signals import ScreeningResult
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import today_jst, utcnow

_log = get_logger("agents.zeele_llm_scout")

# 7 種類 enum
PresetName = Literal[
    "value", "dividend", "momentum", "growth",
    "pullback", "contrarian", "alpha", "growth-value",
]

_VALID_PRESETS: set[str] = {
    "value", "dividend", "momentum", "growth",
    "pullback", "contrarian", "alpha", "growth-value",
}

# 探索量制御
_MAX_LLM_CALLS_PER_BATCH = 30   # 1 バッチで叩く上限
_MAX_INPUT_CHARS = 400          # D2: 入力短縮
_CACHE_TTL_HOURS = 24           # D5: 24h cache

# プロセス内 cache: ticker_hash → (when, result_dict)
_CACHE: dict[str, tuple[dt.datetime, dict[str, Any]]] = {}


def reset_cache() -> None:
    """テスト用 cache リセット。"""
    _CACHE.clear()


@dataclass(frozen=True)
class LLMPresetResult:
    """LLM が 1 銘柄に対して返した判定結果。"""

    ticker: str
    preset: str        # _VALID_PRESETS 内に必ず収まる（H2 強制）
    confidence: float  # 0.0-1.0
    reason: str        # 80 字以内
    used_llm: bool     # True: LLM 呼出 / False: cache hit or fallback


class ZeeleLLMScoutInput(AgentInput):
    """zeele_llm_scout の入力。"""

    # 探索対象 universe ticker（指定なしなら is_active=True 全件から抽出）
    target_tickers: list[str] = Field(default_factory=list)
    # 1 バッチ上限（テスト時に絞る用）
    max_calls: int = _MAX_LLM_CALLS_PER_BATCH
    # per-agent 予算上限（円）。cost_logs(agent=zeele_llm_scout) を集計して実際に効かせる（A-3）。
    daily_budget_jpy: float = 10.0
    monthly_budget_jpy: float = 200.0


class ZeeleLLMScoutOutput(AgentOutput):
    """zeele_llm_scout の出力。"""

    new_candidates: list[dict[str, Any]] = Field(default_factory=list)
    llm_call_count: int = 0
    cache_hit_count: int = 0
    cost_jpy: float = 0.0
    skipped_reason: str = ""


# ----- ヘルパー -----


def _ticker_cache_key(ticker: str, asof: dt.date) -> str:
    """ticker × 日付（JST）でキャッシュキー（同日内は同ティッカーは 1 回のみ）。"""
    raw = f"{ticker}|{asof.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    when, payload = entry
    if (utcnow() - when).total_seconds() > _CACHE_TTL_HOURS * 3600:
        del _CACHE[key]
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    _CACHE[key] = (utcnow(), payload)


def _normalize_preset(raw: Any) -> str:
    """LLM 出力の preset を _VALID_PRESETS に正規化（H2）。"""
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _VALID_PRESETS:
            return s
    return "alpha"


def _agent_cost_jpy(engine: Engine, agent: str, since_date: dt.date) -> float:
    """cost_logs から指定 agent の since_date 以降の累計コスト（円）。per-agent 予算用（A-3）。"""
    with Session(engine) as s:
        stmt = (
            select(func.coalesce(func.sum(CostLog.cost_jpy), 0.0))
            .where(col(CostLog.agent) == agent)
            .where(col(CostLog.date) >= since_date)
        )
        return float(s.exec(stmt).one())


def _normalize_confidence(raw: Any) -> float:
    """confidence を 0.0-1.0 にクランプ（H2）。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _truncate_input(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """D2: 入力短縮。"""
    return text[:max_chars] if text else ""


def _build_prompt(ticker: str, sector: str | None, brief_text: str) -> str:
    """D3: 出力 JSON schema 固定 + ハルシネーション禁止プロンプト。"""
    return (
        "あなたは投資銘柄分類のアシスタントです。\n\n"
        "【厳守事項：ハルシネーション禁止】\n"
        "  - 与えられた銘柄以外の ticker を返さない\n"
        "  - 数値や将来予測を生成しない（preset は与えられた 7 種の中から必ず選ぶ）\n"
        "  - 情報が不十分なら confidence を 0.0 にし reason に「情報不足」と明記\n\n"
        f"対象: ticker={ticker}  sector={sector or 'unknown'}\n"
        f"参考情報（screening + ニュース集計）:\n{_truncate_input(brief_text)}\n\n"
        "上記情報から、以下 7 種の preset のいずれかを選んでください:\n"
        "  - value: PER 低・割安・安定収益\n"
        "  - dividend: 配当利回り高\n"
        "  - momentum: 相対力 Leading + テーマ追い風\n"
        "  - growth: 増収/増益率 高\n"
        "  - pullback: V字 + 株価底打ち転換（押し目）\n"
        "  - contrarian: 逆張り（value_trap 等）\n"
        "  - alpha: 上記非該当（バランス）\n"
        "  - growth-value: growth + value 両立（Asness）\n\n"
        '出力形式: {"preset": "<name>", "confidence": <0.0-1.0>, "reason": "<80字以内>"}'
    )


def call_haiku_preset(ticker: str, prompt: str) -> dict[str, Any] | None:
    """Haiku を呼んで preset 判定を返す。失敗時 None。

    M4.B9 (実 LLM スモーク) で使用。M4.B7 単体テストでは mock で置換可能。
    """
    try:
        from anthropic import Anthropic

        from trading_agent.config import load_settings

        settings = load_settings()
        if not settings.anthropic_api_key:
            return None
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
        match = re.search(r"\{[^}]+\}", text)
        if not match:
            return None
        data = json.loads(match.group(0))
        return {
            "preset": _normalize_preset(data.get("preset")),
            "confidence": _normalize_confidence(data.get("confidence")),
            "reason": str(data.get("reason", ""))[:80],
            "tokens_in": int(getattr(response.usage, "input_tokens", 0) or 0),
            "tokens_out": int(getattr(response.usage, "output_tokens", 0) or 0),
        }
    except Exception as exc:
        _log.warning("haiku_preset_call_failed", ticker=ticker, error_type=type(exc).__name__)
        return None


# ----- エージェント本体 -----


class ZeeleLLMScoutAgent(Agent[ZeeleLLMScoutInput]):
    """ZEELE LLM 探索エージェント。

    universe.is_active=True で screening 候補外の銘柄について Haiku で preset 判定し、
    ZeeleState に upsert する。BudgetGuard / cache / call_count で安全運用。
    """

    name = "zeele_llm_scout"
    description = (
        "Haiku で V字/テーマ/攻め銘柄を探索し、ZEELE プールに preset 付きで追加する"
        "（zeele_curator の決定論的入賞ベースを補完）"
    )
    required_tools: list[str] = []  # 直接 Haiku 同期 API 使用（llm_call MCP 経由しない）
    default_routing = "cold"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: ZeeleLLMScoutInput) -> AgentOutput:
        engine = self._ctx.engine
        max_calls = max(0, min(int(agent_input.max_calls), _MAX_LLM_CALLS_PER_BATCH))
        if max_calls == 0:
            return ZeeleLLMScoutOutput(
                success=True,
                invocation_id=agent_input.invocation_id,
                summary="max_calls=0 のためスキップ",
                skipped_reason="max_calls=0",
            )

        # 1. 探索対象 universe の決定
        targets = self._select_targets(engine, agent_input.target_tickers, max_calls)
        if not targets:
            return ZeeleLLMScoutOutput(
                success=True,
                invocation_id=agent_input.invocation_id,
                summary="探索対象なし",
                skipped_reason="no_target_universe",
            )

        # 2. BudgetGuard 取得
        guard = BudgetGuard(engine)

        # 3. 探索ループ
        new_candidates: list[dict[str, Any]] = []
        llm_call_count = 0
        cache_hit_count = 0
        cost_jpy = 0.0
        today = today_jst()
        # A-3: per-agent 予算（旧 dead param を実装）。当日/当月の自エージェント既消費を集計し、
        # このバッチの累積 cost_jpy と合わせて daily/monthly 上限を超えたら打ち切る。
        month_start = today.replace(day=1)
        agent_day_spent = _agent_cost_jpy(engine, self.name, today)
        agent_month_spent = _agent_cost_jpy(engine, self.name, month_start)

        for ticker, sector, brief_text in targets:
            cache_key = _ticker_cache_key(ticker, today)
            cached = _cache_get(cache_key)
            if cached is not None:
                cache_hit_count += 1
                if cached.get("preset"):
                    new_candidates.append({**cached, "ticker": ticker, "cache": True})
                continue

            # コスト試算: in 500 / out 100 想定で ¥0.15
            estimated = 0.15
            # A-3: per-agent 日次/月次予算（旧 dead param daily/monthly_budget_jpy を実装）
            if agent_day_spent + cost_jpy + estimated > agent_input.daily_budget_jpy:
                self._log.info(
                    "zeele_scout_daily_budget_reached",
                    spent=round(agent_day_spent + cost_jpy, 4),
                    cap=agent_input.daily_budget_jpy,
                )
                break
            if agent_month_spent + cost_jpy + estimated > agent_input.monthly_budget_jpy:
                self._log.info(
                    "zeele_scout_monthly_budget_reached",
                    spent=round(agent_month_spent + cost_jpy, 4),
                    cap=agent_input.monthly_budget_jpy,
                )
                break
            # 全体 BudgetGuard（共有 ¥500/¥5000）でも二重ガード
            ok, msg = guard.can_proceed(estimated, routing_hint=None)
            if not ok:
                self._log.info("zeele_scout_budget_skip", ticker=ticker, msg=msg)
                break

            prompt = _build_prompt(ticker, sector, brief_text)
            result = call_haiku_preset(ticker, prompt)
            llm_call_count += 1
            if result is None:
                # 失敗時は alpha フォールバック（推測しない）
                continue

            preset = result["preset"]
            confidence = result["confidence"]
            reason = result["reason"]
            tokens_in = result["tokens_in"]
            tokens_out = result["tokens_out"]

            # 実コスト記録（ハルシネーション H2: 値域すでに正規化済）
            actual_cost = (tokens_in / 1000) * 0.15 + (tokens_out / 1000) * 0.75
            cost_jpy += actual_cost

            cache_payload = {
                "preset": preset,
                "confidence": confidence,
                "reason": reason,
            }
            _cache_put(cache_key, cache_payload)

            try:
                with Session(engine) as s:
                    s.add(
                        CostLog(
                            date=today,
                            model="haiku",
                            agent=self.name,
                            purpose="zeele_preset_scout",
                            tokens_in=max(1, tokens_in),
                            tokens_out=max(1, tokens_out),
                            cost_usd=actual_cost / 150.0,
                            cost_jpy=actual_cost,
                            invocation_id=f"zls_{ticker}_{cache_key[:8]}",
                        )
                    )
                    s.commit()
            except Exception as exc:
                self._log.warning("zeele_scout_cost_log_failed", error_type=type(exc).__name__)

            new_candidates.append({**cache_payload, "ticker": ticker, "cache": False})

        # 4. ZeeleState 反映（confidence >= 0.5 のみ）
        upserted = 0
        with Session(engine) as s:
            for cand in new_candidates:
                if cand.get("confidence", 0.0) < 0.5:
                    continue
                ticker = cand["ticker"]
                u = s.exec(select(Universe).where(col(Universe.ticker) == ticker)).first()
                if u is None or not u.is_active:
                    continue  # C2: universe 外 / 上場廃止は捨てる
                state = s.exec(select(ZeeleState).where(col(ZeeleState.ticker) == ticker)).first()
                if state is None:
                    state = ZeeleState(
                        ticker=ticker,
                        entered_at=today,
                        weeks_in_zeele=1,
                        consecutive_weeks=1,
                        last_screened_at=utcnow(),
                        preset=cand["preset"],
                        structural_thesis=f"LLM 探索発掘: {cand.get('reason', '')[:80]}",
                        reference_score=cand["confidence"] * 100,
                        is_active=True,
                    )
                    s.add(state)
                    upserted += 1
                else:
                    state.preset = cand["preset"]
                    state.reference_score = cand["confidence"] * 100
                    state.last_screened_at = utcnow()
                    state.is_active = True
                    state.updated_at = utcnow()
                    if cand.get("reason"):
                        state.structural_thesis = (
                            f"LLM 探索更新: {cand['reason'][:80]}"
                        )
                    s.add(state)
                    upserted += 1
            s.commit()

        return ZeeleLLMScoutOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=(
                f"LLM 探索 {llm_call_count} 回 / cache {cache_hit_count} 件 / "
                f"upsert {upserted} 件 / コスト ¥{cost_jpy:.2f}"
            ),
            new_candidates=new_candidates,
            llm_call_count=llm_call_count,
            cache_hit_count=cache_hit_count,
            cost_jpy=cost_jpy,
        )

    def _select_targets(
        self,
        engine,
        explicit_tickers: list[str],
        max_calls: int,
    ) -> list[tuple[str, str | None, str]]:
        """探索対象を選定。

        - explicit_tickers が指定されたらそれを優先
        - そうでなければ「universe.is_active=True かつ ZeeleState に存在しない」を抽出
        - 各銘柄に対し screening の最新 details を文字列化して brief_text を作る

        Returns:
            [(ticker, sector, brief_text), ...]
        """
        with Session(engine) as s:
            if explicit_tickers:
                tickers_to_check = explicit_tickers[:max_calls]
            else:
                # universe.is_active=True で ZeeleState に未登録のもの
                all_active = list(
                    s.exec(
                        select(Universe).where(col(Universe.is_active)).limit(max_calls * 5)
                    )
                )
                already_in_zeele = {
                    z.ticker
                    for z in s.exec(select(ZeeleState).where(col(ZeeleState.is_active)))
                }
                tickers_to_check = [
                    u.ticker for u in all_active if u.ticker not in already_in_zeele
                ][:max_calls]

            results: list[tuple[str, str | None, str]] = []
            for ticker in tickers_to_check:
                u = s.exec(select(Universe).where(col(Universe.ticker) == ticker)).first()
                if u is None or not u.is_active:
                    continue
                # screening 最新値を brief_text に
                sr = s.exec(
                    select(ScreeningResult)
                    .where(col(ScreeningResult.ticker) == ticker)
                    .order_by(col(ScreeningResult.screened_at).desc())
                ).first()
                if sr is None:
                    brief = "screening データなし（探索対象として LLM 判定）"
                else:
                    brief = (
                        f"v_shape_score={sr.v_shape_score:.0f} theme_score={sr.theme_score:.0f} "
                        f"composite={sr.composite_score:.0f} "
                        f"v_details={sr.v_shape_details} theme_details={sr.theme_details}"
                    )
                results.append((ticker, u.sector, brief))
            return results
