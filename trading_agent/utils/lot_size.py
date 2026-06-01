"""銘柄の取引単元と実弾モード判定（v2.8）。

JP 株（4 桁数字 ticker）は単元株 = 100 株が基本。
米国株は 1 株単位。

実弾モード（WILLE_LIVE_MODE=1）では:
  - paper_exec の shares 計算を lot_size 倍数に丸める
  - ds_scout の min_budget を lot_size × price に変える
  - 単元未満で買えない銘柄は申請・fill から除外
"""

from __future__ import annotations

import os


# v2.10: broker_provider 設計（単元未満株対応 broker への拡張）
# moomoo: 単元株（JP=100株, US=1株）
# sbi / rakuten / monex: 単元未満株対応（JP も 1 株単位で取引可能、S 株 / かぶミニ等）
# fractional: 仮想 broker（lot_size=1 強制、ペーパー検証で「もし SBI 使ったら」を計算）

BROKER_PROVIDERS: set[str] = {"moomoo", "sbi", "rakuten", "monex", "fractional"}

# 単元未満株対応 broker（JP 株も 1 株単位で買える）
_FRACTIONAL_PROVIDERS: set[str] = {"sbi", "rakuten", "monex", "fractional"}


def get_broker_provider() -> str:
    """現在の broker_provider を返す。

    優先順位:
      1. WILLE_BROKER_PROVIDER 環境変数
      2. data/wille_settings.json の broker_provider
      3. デフォルト "moomoo"（既存挙動）
    """
    env = os.environ.get("WILLE_BROKER_PROVIDER")
    if env in BROKER_PROVIDERS:
        return env
    try:
        import json
        from pathlib import Path

        path = Path(_BROKER_MODE_FILE)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            prov = data.get("broker_provider")
            if prov in BROKER_PROVIDERS:
                return prov
    except Exception:
        pass
    return "moomoo"


def set_broker_provider(provider: str) -> None:
    """broker_provider をファイルに永続化（UI トグル経由）。"""
    import json
    from pathlib import Path

    if provider not in BROKER_PROVIDERS:
        raise ValueError(
            f"provider must be one of {sorted(BROKER_PROVIDERS)}: {provider}"
        )
    path = Path(_BROKER_MODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["broker_provider"] = provider
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def supports_fractional_shares(provider: str | None = None) -> bool:
    """現在の broker が単元未満株に対応してるか。"""
    p = provider or get_broker_provider()
    return p in _FRACTIONAL_PROVIDERS


def get_lot_size(ticker: str, provider: str | None = None) -> int:
    """銘柄の取引単元を返す。

    v2.10: broker_provider 対応
      - moomoo: JP=100 株 / US=1 株（単元株）
      - sbi/rakuten/monex/fractional: 全銘柄 1 株単位（単元未満株対応）
    """
    p = provider or get_broker_provider()
    if p in _FRACTIONAL_PROVIDERS:
        return 1
    if len(ticker) == 4 and ticker.isdigit():
        return 100
    return 1


def is_lot_mode() -> bool:
    """単元株モードか判定（デフォルト True）。

    用語整理（v2.8）:
      - **Paper モード（検証）**: 1 株単位 fill / DB 記録のみ
      - **単元株モード（検証）**: JP 100 株単位 fill / DB 記録のみ（=本流）
      - **moomoo Live（本番）**: 単元株単位 + moomoo OpenD 経由の実発注

    Paper モードに切替: WILLE_PAPER_MODE=1
    """
    return os.environ.get("WILLE_PAPER_MODE", "0") != "1"


def is_live_mode() -> bool:
    """[DEPRECATED] 旧 API。is_lot_mode の別名。コード互換のため残置。"""
    return is_lot_mode()


_BROKER_MODE_FILE = "data/wille_settings.json"


def get_broker_mode() -> str:
    """現在の broker_mode を返す（"paper" or "live"）。

    優先順位:
      1. 環境変数 WILLE_BROKER_LIVE=1 → "live"（テスト・CLI 用の強制切替）
      2. data/wille_settings.json の broker_mode（UI から切替）
      3. デフォルト "paper"
    """
    if os.environ.get("WILLE_BROKER_LIVE", "0") == "1":
        return "live"
    try:
        import json
        from pathlib import Path

        path = Path(_BROKER_MODE_FILE)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            mode = data.get("broker_mode")
            if mode in ("paper", "live"):
                return mode
    except Exception:
        pass
    return "paper"


def set_broker_mode(mode: str) -> None:
    """broker_mode をファイルに永続化（UI トグル経由で呼ばれる）。"""
    import json
    from pathlib import Path

    if mode not in ("paper", "live"):
        raise ValueError(f"mode must be 'paper' or 'live': {mode}")
    path = Path(_BROKER_MODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["broker_mode"] = mode
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_moomoo_live() -> bool:
    """**moomoo 実発注モード**（本番・リアルマネー）か判定。

    True なら paper_exec が moomoo OpenD 経由で実発注する。
    False なら DB 記録のみ（Paper 検証）or 他 broker（楽天/SBI/kabu.com 等）。

    v2.10 修正: broker_provider="moomoo" の時のみ True を返す。
    broker_provider="rakuten"/"sbi"/"kabucom" 等で broker_mode="live" でも、
    moomoo 経路には入らない（誤発注防止）。

    切替方法:
      - UI サイドバーのトグル（永続化: data/wille_settings.json）
      - WILLE_BROKER_LIVE=1 + broker_provider=moomoo
    """
    return get_broker_mode() == "live" and get_broker_provider() == "moomoo"


# === v2.10 Phase J: automation_mode（人間決済 vs 自動売買） ====================
# broker_mode (paper/live) とは独立した軸:
#   - broker_mode: 「DB だけ記録」vs「moomoo 実発注」
#   - automation_mode: 「人間決済」vs「機械自動」
#
# 真理値表:
#   broker=paper × automation=manual : ペーパー検証・人間判断（既定）
#   broker=paper × automation=auto   : ペーパー完全自動運用テスト
#   broker=live  × automation=manual : 実弾だが人間決済（半自動）
#   broker=live  × automation=auto   : 完全自動売買（本番）

def get_automation_mode() -> str:
    """現在の automation_mode を返す（"manual" or "auto"）。

    優先順位:
      1. 環境変数 WILLE_AUTOMATION_AUTO=1 → "auto"（テスト・CLI 用の強制切替）
      2. data/wille_settings.json の automation_mode（UI から切替）
      3. デフォルト "manual"（安全側）
    """
    if os.environ.get("WILLE_AUTOMATION_AUTO", "0") == "1":
        return "auto"
    try:
        import json
        from pathlib import Path

        path = Path(_BROKER_MODE_FILE)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            mode = data.get("automation_mode")
            if mode in ("manual", "auto"):
                return mode
    except Exception:
        pass
    return "manual"


def set_automation_mode(mode: str) -> None:
    """automation_mode をファイルに永続化（UI トグル経由）。"""
    import json
    from pathlib import Path

    if mode not in ("manual", "auto"):
        raise ValueError(f"mode must be 'manual' or 'auto': {mode}")
    path = Path(_BROKER_MODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["automation_mode"] = mode
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_auto_mode() -> bool:
    """完全自動売買モードか判定（モード分岐用の short-cut）。

    True なら:
      - 朝バッチで close_due / pyramid_fill / 自動 fill が走る
      - 完全自動セーフティ（correlation/DD/factor）が ON
      - 異常検知 + HALT が ON
    False なら:
      - 朝バッチは Decision 登録までで止まる
      - 実行は misato_dispatch.py 経由（人間トリガー）
      - 完全自動セーフティは OFF（dashboard で人間判断）
    """
    return get_automation_mode() == "auto"


# === v2.10 Phase I-12: 段階的実弾移行 ============================================
# broker_mode=live への移行を 5 段階に分けて、各段階で 1 ポジションの最大金額を制限。
# tier_1 (¥10k) から始め、運用安定を確認しながら段階的に昇格。
# 昇格条件は別途運用ルール（連続日数・勝率・DD 閾値）で人間が判断する。

LIVE_TIER_LIMITS: dict[str, int | None] = {
    "tier_1": 10_000,        # ¥10k: 動作確認・手数料負け前提
    "tier_2": 50_000,        # ¥50k: 1 銘柄を実弾で運用
    "tier_3": 200_000,       # ¥200k: 数銘柄に展開
    "tier_4": 1_000_000,     # ¥1M: 通常運用に近い規模
    "full": None,            # 無制限（既存 get_max_lot_pct ロジック）
}


def get_live_tier() -> str:
    """live モードの段階移行 tier を返す。

    優先順位:
      1. WILLE_LIVE_TIER 環境変数 (tier_1〜tier_4 | full)
      2. data/wille_settings.json の live_tier
      3. デフォルト "tier_1"（最小予算・最も安全）
    """
    env = os.environ.get("WILLE_LIVE_TIER")
    if env in LIVE_TIER_LIMITS:
        return env
    try:
        import json
        from pathlib import Path

        path = Path(_BROKER_MODE_FILE)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            tier = data.get("live_tier")
            if tier in LIVE_TIER_LIMITS:
                return tier
    except Exception:
        pass
    return "tier_1"


def set_live_tier(tier: str) -> None:
    """live_tier をファイルに永続化（UI トグル経由）。"""
    import json
    from pathlib import Path

    if tier not in LIVE_TIER_LIMITS:
        raise ValueError(
            f"tier must be one of {list(LIVE_TIER_LIMITS)}: {tier}"
        )
    path = Path(_BROKER_MODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["live_tier"] = tier
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_tier_max_jpy() -> float | None:
    """現在の live_tier の 1 ポジション上限金額（円）。None なら無制限。

    broker_mode=paper 時も値は返すが、get_max_lot_cost_jpy が paper では無視する。
    """
    limit = LIVE_TIER_LIMITS.get(get_live_tier())
    return float(limit) if limit is not None else None


def effective_lot_size(ticker: str) -> int:
    """実弾モードならその銘柄の lot_size、Paper モードなら 1 を返す。

    paper_exec の shares 計算で「単元株単位に丸めるか」の判定に使う。
    """
    if not is_live_mode():
        return 1
    return get_lot_size(ticker)


def get_max_lot_pct(treasury_jpy: float | None = None) -> float:
    """1 ポジションに投じてよい比率（v2.10: 攻めるが守る設計）。

    環境変数 WILLE_MAX_LOT_PCT が指定されていればそれを使う（強制固定）。
    指定がなければ予算規模に応じて自動調整:
      treasury ≤ ¥200k  → 50%（少額: 攻め型・候補確保優先・stop_loss と cash 保持で守る）
      treasury ≤ ¥1M   → 25%（中額: バランス型）
      treasury > ¥1M   → 10%（大額: 分散型）

    Args:
        treasury_jpy: 適応判定用の預かり金。None なら 50% (少額デフォルト)。
    """
    env_pct = os.environ.get("WILLE_MAX_LOT_PCT")
    if env_pct:
        try:
            pct = float(env_pct)
            return max(0.01, min(1.0, pct))
        except (TypeError, ValueError):
            pass
    # 適応的決定
    if treasury_jpy is None or treasury_jpy <= 0:
        return 0.50  # 少額デフォルト
    if treasury_jpy <= 200_000:
        return 0.50
    if treasury_jpy <= 1_000_000:
        return 0.25
    return 0.10


def get_max_lot_cost_jpy(available_budget_jpy: float | None = None) -> float:
    """1 ポジション（1 銘柄）に投じてよい上限金額（円）。

    優先順位:
      1. WILLE_MAX_LOT_JPY が設定されていれば、その金額を直接使う（割合は無視）
      2. WILLE_MAX_LOT_PCT が設定されていれば available_budget × pct
      3. Paper モードは無制限（ただし tier 上限は適用しない）
      4. v2.10 Phase I-12: broker=live なら tier 上限と min を取る

    Args:
        available_budget_jpy: 購入可能金額（treasury 残高など）
    """
    if not is_live_mode():
        return float("inf")
    # 1. 金額直接指定が優先
    jpy_env = os.environ.get("WILLE_MAX_LOT_JPY")
    if jpy_env:
        try:
            v = float(jpy_env)
            if v > 0:
                base = v
            else:
                base = 0.0
        except ValueError:
            base = 0.0
    elif available_budget_jpy is None or available_budget_jpy <= 0:
        base = 0.0
    else:
        # 2. 割合ベース（v2.9: 預かり金規模で適応的に変動）
        base = float(available_budget_jpy) * get_max_lot_pct(available_budget_jpy)

    # v2.10 Phase I-12: broker_mode="live" なら tier 上限を適用（broker_provider 問わず）
    # 楽天/SBI/kabu.com の実弾でも tier 段階制限を効かせる安全弁
    if get_broker_mode() == "live":
        tier_max = get_tier_max_jpy()
        if tier_max is not None:
            return min(base, tier_max)
    return base


def can_afford_one_lot(
    ticker: str, price_per_share: float, available_budget_jpy: float | None = None
) -> bool:
    """この銘柄を 1 単元買えるか判定（実弾モード時のみ意味あり）。

    実弾モード:
      1 単元 = price × lot_size ≤ get_max_lot_cost_jpy(available_budget_jpy)
    Paper モード:
      常に True
    """
    if not is_live_mode():
        return True
    lot = get_lot_size(ticker)
    lot_cost = price_per_share * lot
    return lot_cost <= get_max_lot_cost_jpy(available_budget_jpy)
