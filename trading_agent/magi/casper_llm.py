"""CASPER 本格判定（P3-7：LLM文脈解釈）。

決定論版 `judges.casper`（キーワード計数＝確信度 低）の上に、Sonnet による文脈解釈を
**オプトインで**重ねる。LLMは渡された記事/開示の見出し・要約**だけ**を読み、方向(verdict)・
確度(confidence)・根拠文(reason)を出す。**数値・新事実は生成させない（R5）**。

安全側の設計：
- 材料0件 → 決定論版 `casper()`（na）をそのまま返す（捏造しない）。
- 予算超過 / パース失敗 / 接続失敗 / クライアント未設定 → すべて決定論版にフォールバック。
  ＝LLMが使えない・暴れた時でも、CASPER は必ず素材に根ざした判定を返す。
- verdict/confidence は列挙値のみ許容。範囲外は破棄してフォールバック。

`run_judges`（同期・三権独立）は変えない。本関数は build_snapshot / DAG（非同期）から
CASPER の判定だけを格上げするために使う。
"""

from __future__ import annotations

import json
from typing import Any

from trading_agent.magi.judges import _refs_to_json, casper
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallTool
from trading_agent.models.magi import JudgeVerdict
from trading_agent.utils.logger import get_logger

_ALLOWED_VERDICT = {"buy", "hold", "warn", "na"}
_ALLOWED_CONF = {"高", "中", "低"}
_MAX_ARTICLES = 15  # コスト抑制：直近の主要材料のみ渡す

_SYSTEM = (
    "あなたは投資判断システムMAGIの「CASPER（文脈審判）」です。\n"
    "渡されたニュース/開示の見出しと要約だけを根拠に、対象銘柄への文脈的な可否を判定します。\n\n"
    "厳守ルール:\n"
    "- 与えられた材料に書かれていない事実・数値・将来予測を創作しない。\n"
    "- 価格目標やEPS等の数値を生成しない。\n"
    "- 判定は材料が示す『方向』と『確度』のみ。材料が方向を示さない/相反するなら hold。\n"
    "- 出力は厳密なJSONのみ。前後に説明文やコードフェンスを付けない。\n\n"
    "出力スキーマ:\n"
    '{"verdict":"buy|hold|warn|na","confidence":"高|中|低","reason":"日本語1〜2文。材料に基づく根拠。"}\n'
    "verdict: buy=ポジ材料優勢 / warn=ネガ材料優勢・要警戒 / "
    "hold=中立or拮抗 / na=方向を判断できる材料がない\n"
    "confidence: 材料の数・具体性・一貫性が高いほど高。少数・曖昧・矛盾は低。"
)


def _gather(news: Any, disclosure: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    """news / disclosure から記事・出典・最新時点を集約（casper と同じ取り回し）。"""
    items: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    asof = None
    for out in (news, disclosure):
        if out is None:
            continue
        arts = getattr(out, "articles", None)
        if arts is None:
            arts = getattr(out, "disclosures", None) or []
        items.extend(arts)
        refs.extend(_refs_to_json(getattr(out, "source_refs", [])))
        a = getattr(out, "data_asof", None)
        if a is not None and (asof is None or a > asof):
            asof = a
    return items, refs, asof


def _build_user_prompt(ticker: str, items: list[dict[str, Any]]) -> str:
    lines = [f"対象銘柄: {ticker}", f"直近の材料（{len(items)}件）:"]
    for i, it in enumerate(items[:_MAX_ARTICLES], start=1):
        src = str(it.get("source") or "").strip()
        title = str(it.get("title") or "").strip()
        summary = str(it.get("summary") or "").strip().replace("\n", " ")[:200]
        head = f"{i}. [{src}] {title}" if src else f"{i}. {title}"
        lines.append(f"{head} — {summary}" if summary else head)
    lines.append("上記だけを根拠に、JSONで判定してください。")
    return "\n".join(lines)


def _parse(text: str) -> tuple[str, str, str] | None:
    """LLM応答（JSON）を verdict/confidence/reason に。コードフェンス除去・列挙値検証。"""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body[:4].lower() == "json":
            body = body[4:]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(body[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict", "")).strip().lower()
    confidence = str(obj.get("confidence", "")).strip()
    reason = str(obj.get("reason", "")).strip()
    if verdict not in _ALLOWED_VERDICT or confidence not in _ALLOWED_CONF or not reason:
        return None
    return verdict, confidence, reason


async def casper_llm(
    ticker: str,
    *,
    news: Any = None,
    disclosure: Any = None,
    llm_tool: LLMCallTool,
    invocation_id: str | None = None,
) -> JudgeVerdict:
    """CASPER をSonnet解釈に格上げする。失敗時は決定論版にフォールバック。"""
    log = get_logger("magi").bind(judge="CASPER", ticker=ticker)
    items, refs, asof = _gather(news, disclosure)

    deterministic = casper(ticker, news=news, disclosure=disclosure)
    if not items:
        return deterministic  # 材料0＝na（捏造しない）

    try:
        out = await llm_tool.execute(
            LLMCallInput(
                prompt=_build_user_prompt(ticker, items),
                system=_SYSTEM,
                purpose="analysis",
                routing_hint="hot",  # CASPER=Sonnet
                max_tokens=300,
                temperature=0.0,
                agent="casper",
                invocation_id=invocation_id,
            )
        )
    except Exception as exc:  # 接続/認証/レート → 決定論版で継続
        log.warning("casper_llm_failed", error=str(exc))
        return deterministic

    if not out.success:
        log.warning("casper_llm_unavailable", reason=out.error)
        return deterministic

    parsed = _parse(getattr(out, "response", "") or "")
    if parsed is None:
        log.warning("casper_llm_unparseable")
        return deterministic

    verdict, confidence, reason = parsed
    return JudgeVerdict(
        ticker=ticker,
        judge="CASPER",
        verdict=verdict,
        confidence=confidence,
        reason=reason,
        source_refs=refs,
        data_asof=asof,
        verdict_source="llm",  # v2.2 TASK-M9: Sonnet 解釈で判定
    )
