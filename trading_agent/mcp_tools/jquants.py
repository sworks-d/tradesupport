"""J-Quants V2 API クライアント（v2.10）。

公式 SDK `jquants-api-client` (v2.1.0) を薄くラップする。
- base URL / 認証ヘッダー / レスポンス schema は **SDK に任せる**
  → 自前 httpx 実装による仕様取り違え（ハルシネーション温床）を排除。
- 取れない時は **空 list** を返す（埋めない・推測しない）。

データソース優先順位（D-25 + v2.10 拡張）:
  JP 株: 1. J-Quants (公式正本) → 2. yfinance (フォールバック)
  US ETF: 1. yfinance (J-Quants は JP のみ)

V2 認証:
  - `.env` の JQUANTS_REFRESH_TOKEN（フィールド名は V1 名残）に API key を入れる
  - 環境変数 JQUANTS_API_KEY でも可（SDK の標準）

主要 SDK メソッド（要約）:
  - get_eq_master          : 銘柄マスタ（実在検証）
  - get_eq_bars_daily      : 日次株価 OHLCV（単一銘柄）
  - get_eq_bars_daily_range: 日次株価 範囲取得（期間指定）
  - get_fin_summary        : 決算サマリ
  - get_eq_earnings_cal    : 決算発表予定
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from trading_agent.config import load_settings
from trading_agent.utils.logger import get_logger

_log = get_logger("mcp_tools.jquants")


class JQuantsClient:
    """公式 SDK `jquants-api-client` の薄いラッパー。

    使い方:
        client = JQuantsClient()
        master = client.listed_info(ticker="7203")
        bars = client.daily_quotes(ticker="7203", from_date=dt.date(2026,5,1))
        fins = client.statements(ticker="7203")
    """

    def __init__(self, api_key: str | None = None) -> None:
        import jquantsapi  # noqa: F401 — 遅延 import（テスト環境で SDK 無しでも import 可）

        s = load_settings()
        # 既存 env 変数名 (JQUANTS_REFRESH_TOKEN) を流用（フィールドは新 V2 API key を格納）
        self._api_key = api_key or s.jquants_refresh_token
        if not self._api_key:
            raise ValueError("jquants API key is not configured (.env: JQUANTS_REFRESH_TOKEN)")
        # SDK は env JQUANTS_API_KEY も読むが、ここでは明示的に渡して曖昧さを排除
        self._cli = jquantsapi.ClientV2(api_key=self._api_key)

    # === 共通: DataFrame → list[dict] =====================================

    @staticmethod
    def _df_to_records(df: Any) -> list[dict[str, Any]]:
        """SDK が返す DataFrame を list[dict] に変換。None/空 は []。"""
        if df is None:
            return []
        try:
            # 空 DataFrame の判定
            if hasattr(df, "empty") and df.empty:
                return []
            if hasattr(df, "to_dict"):
                return df.to_dict(orient="records")
        except Exception as exc:
            _log.warning(
                "jquants_df_conversion_failed", error_type=type(exc).__name__
            )
        return []

    # === Public APIs =====================================================

    def listed_info(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """上場銘柄マスタ（実在検証・新規上場暫定コード対応）。

        Returns:
            銘柄情報リスト。失敗時は空リスト（ハルシネーション防止）。
            主なキー (V2 仕様): Code, CompanyName, MarketCode, MarketCodeName,
                              Sector17Code, Sector33Code, ScaleCategory, など。
        """
        try:
            if ticker:
                # SDK の get_eq_master は code 指定可（要確認）
                df = self._cli.get_eq_master(code=ticker)  # type: ignore[call-arg]
            else:
                df = self._cli.get_eq_master()
            return self._df_to_records(df)
        except Exception as exc:
            _log.warning(
                "jquants_listed_info_failed",
                ticker=ticker,
                error_type=type(exc).__name__,
            )
            return []

    def daily_quotes(
        self,
        ticker: str,
        *,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
    ) -> list[dict[str, Any]]:
        """日次株価（OHLCV）。

        Returns:
            日次株価リスト。失敗時は空リスト。
            実列名 (jquantsapi 2.1.0 ClientV2・2026-06 dry-read 確認): Date, Code,
            O, H, L, C, Vo(出来高), Va, **AdjC(調整済終値)**, AdjO/AdjH/AdjL, AdjVo,
            AdjFactor, UL/LL(上限/下限)。※調整済終値は AdjC（旧 "AdjustmentClose" は誤り）。
            ※ Free は直近 12 週は遅延で空、履歴 2 年。
        """
        try:
            kwargs: dict[str, Any] = {"code": ticker}
            if from_date:
                kwargs["from_yyyymmdd"] = from_date.strftime("%Y%m%d")
            if to_date:
                kwargs["to_yyyymmdd"] = to_date.strftime("%Y%m%d")
            df = self._cli.get_eq_bars_daily(**kwargs)  # type: ignore[call-arg]
            return self._df_to_records(df)
        except Exception as exc:
            _log.warning(
                "jquants_daily_quotes_failed",
                ticker=ticker,
                error_type=type(exc).__name__,
            )
            return []

    def statements(self, ticker: str) -> list[dict[str, Any]]:
        """財務諸表サマリ。

        Returns:
            四半期財務リスト。失敗時は空リスト。
            実列名 (jquantsapi 2.1.0・2026-06 dry-read 確認): **DiscDate(開示日・Timestamp)**,
            Code, CurPerEn(当期末), Sales, OP(営業利益), NP(純利益), TA(総資産), CFO,
            EPS, BPS, Eq, NC*(非連結), F*/Nx*(予想), Div*(配当) など。
            ※ 開示日は DiscDate（旧 "DisclosedDate" 表記は誤り）。財務値は Sales/OP/NP/TA/CFO。
            ※ Free は Summary のみ（cogs/在庫/負債等は無く Beneish/Altman は na/warn 化）。
        """
        try:
            df = self._cli.get_fin_summary(code=ticker)  # type: ignore[call-arg]
            return self._df_to_records(df)
        except Exception as exc:
            _log.warning(
                "jquants_statements_failed",
                ticker=ticker,
                error_type=type(exc).__name__,
            )
            return []


# === シングルトン経由のアクセス（軽量利用向け） =========================

_singleton: JQuantsClient | None = None


def get_default_client() -> JQuantsClient | None:
    """環境にトークンがあればシングルトンを返す。

    None を返すケース:
      - JQUANTS_REFRESH_TOKEN が未設定
      - SDK の import 失敗（インストールされていない）
    """
    global _singleton
    if _singleton is None:
        try:
            _singleton = JQuantsClient()
        except (ValueError, ImportError):
            return None
    return _singleton
