"""moomoo OpenAPI ブローカー（口座・保有・資金）。Phase 1.2。

OpenD（ローカルゲートウェイ）経由で positions / account を取得する。
SDK は ``moomoo``（moomoo-api）/ ``futu``（futu-api）どちらでも動くよう遅延importで吸収。
SDK未導入 / OpenD未起動 / 口座未接続では ``BrokerUnavailable`` を送出する
（呼び出し側がスタンドインへフォールバックする）。

参照: https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html
注意: OpenD と実口座（またはペーパー口座 SIMULATE）での疎通確認は口座開設後に行う。
      本実装はドキュメント仕様準拠（position_list_query / accinfo_query）。
"""

from __future__ import annotations

from typing import Any

from trading_agent.brokers.base import Account, BrokerUnavailable, Position
from trading_agent.utils.logger import get_logger

_log = get_logger("broker.moomoo")


def _import_sdk() -> Any:
    """moomoo / futu SDK を遅延importする（同一API）。未導入なら BrokerUnavailable。"""
    try:
        import moomoo as sdk  # moomoo-api
    except ImportError:
        try:
            import futu as sdk  # futu-api（同一API）
        except ImportError as exc:
            raise BrokerUnavailable("moomoo/futu SDK が未導入（pip install moomoo-api）") from exc
    return sdk


def _f(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class MoomooBroker:
    """moomoo OpenAPI 経由のブローカー。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 11111,
        # paper-first（liveは明示切替）。※JPはAPI上REALのみ（SIMULATE非対応）
        trd_env: str = "SIMULATE",
        markets: tuple[str, ...] = ("US", "JP"),
        # moomoo JP。2026-05-26 実環境でJP/REAL疎通OK（保有/口座読取）
        security_firm: str = "FUTUJP",
        # accinfo_query の換算通貨。JP口座は JPY 必須（未指定だと変換エラー）
        currency: str = "JPY",
    ) -> None:
        self._host = host
        self._port = port
        self._trd_env = trd_env
        self._markets = markets
        self._security_firm = security_firm
        self._currency = currency

    @classmethod
    def from_settings(cls, settings: Any) -> MoomooBroker:
        """Settings（config.py）から構築する。

        JP は moomoo SIMULATE 非対応のため、paper / live とも **REAL 口座を読み取る**設計に統一。
        紙運用の仮想入金は `load_account` の overlay で表現（実弾化は `TRADING_MODE=live` のみで
        切替＝overlay が自動で外れる）。
        """
        markets = tuple(
            m.strip()
            for m in str(getattr(settings, "moomoo_markets", "US,JP")).split(",")
            if m.strip()
        )
        return cls(
            host=getattr(settings, "moomoo_opend_host", "127.0.0.1"),
            port=int(getattr(settings, "moomoo_opend_port", 11111)),
            trd_env="REAL",
            markets=markets or ("US", "JP"),
            security_firm=getattr(settings, "moomoo_security_firm", "FUTUJP"),
            currency=getattr(settings, "moomoo_currency", "JPY"),
        )

    def get_positions(self) -> list[Position]:
        """各市場の保有を取得。市場ごとの失敗（権限なし・環境非対応）は握りつぶして継続。

        OpenD に1市場も接続できない時のみ BrokerUnavailable。空（保有0）は正常に [] を返す。
        """
        sdk = _import_sdk()
        positions: list[Position] = []
        connected = False
        for market in self._markets:
            try:
                ctx = self._open_ctx(sdk, market)
            except BrokerUnavailable as exc:
                _log.warning("ctx_failed", market=market, reason=str(exc))
                continue
            connected = True
            try:
                ret, data = ctx.position_list_query(
                    trd_env=getattr(sdk.TrdEnv, self._trd_env), refresh_cache=True
                )
                if ret == sdk.RET_OK:
                    positions.extend(self._parse(data))
                else:
                    _log.warning(
                        "position_query_skip", market=market, env=self._trd_env, reason=str(data)
                    )
            except Exception as exc:  # 個別市場のクエリ失敗は継続
                _log.warning("position_query_exc", market=market, error=str(exc))
            finally:
                ctx.close()
        if not connected:
            raise BrokerUnavailable("OpenD に接続できない（全市場でコンテキスト生成失敗）")
        return positions

    def get_account(self) -> Account | None:
        sdk = _import_sdk()
        ctx = self._open_ctx(sdk, self._markets[0])
        try:
            ret, data = ctx.accinfo_query(
                trd_env=getattr(sdk.TrdEnv, self._trd_env),
                currency=getattr(sdk.Currency, self._currency),
            )
        except Exception as exc:
            raise BrokerUnavailable(f"accinfo_query 例外: {exc}") from exc
        finally:
            ctx.close()
        if ret != sdk.RET_OK or len(data) == 0:
            return None
        row = data.iloc[0]
        return Account(
            cash=_f(row.get("cash")) or 0.0,
            total_assets=_f(row.get("total_assets")) or 0.0,
            currency=str(row.get("currency", "")),
        )

    def _open_ctx(self, sdk: Any, market: str) -> Any:
        try:
            return sdk.OpenSecTradeContext(
                filter_trdmarket=getattr(sdk.TrdMarket, market),
                host=self._host,
                port=self._port,
                security_firm=getattr(sdk.SecurityFirm, self._security_firm),
            )
        except Exception as exc:  # OpenD未起動・接続失敗
            raise BrokerUnavailable(f"OpenD 接続失敗({market}): {exc}") from exc

    # ===========================================================
    # v2.8: 接続テスト + 実発注（裏で構築・WILLE_BROKER_LIVE=1 で発動）
    # ===========================================================

    def is_connected(self) -> dict[str, object]:
        """OpenD 接続状態を確認する（軽量・読み取りのみ）。

        Returns:
            {
                "connected": bool,
                "sdk_available": bool,
                "opend_reachable": bool,
                "account_readable": bool,
                "error": str | None,
            }
        """
        result: dict[str, object] = {
            "connected": False,
            "sdk_available": False,
            "opend_reachable": False,
            "account_readable": False,
            "error": None,
        }
        try:
            sdk = _import_sdk()
            result["sdk_available"] = True
        except BrokerUnavailable as exc:
            result["error"] = str(exc)
            return result

        try:
            ctx = self._open_ctx(sdk, self._markets[0])
            result["opend_reachable"] = True
        except BrokerUnavailable as exc:
            result["error"] = str(exc)
            return result

        try:
            ret, _ = ctx.accinfo_query(
                trd_env=getattr(sdk.TrdEnv, self._trd_env),
                currency=getattr(sdk.Currency, self._currency),
            )
            if ret == sdk.RET_OK:
                result["account_readable"] = True
                result["connected"] = True
            else:
                result["error"] = "accinfo_query failed"
        except Exception as exc:
            result["error"] = f"accinfo exception: {exc}"
        finally:
            try:
                ctx.close()
            except Exception:
                pass
        return result

    def place_order(
        self,
        ticker: str,
        shares: int,
        *,
        side: str = "buy",
        market: str = "JP",
        order_type: str = "MARKET",
        trd_pwd: str | None = None,
    ) -> dict[str, object]:
        """moomoo OpenD 経由で実発注（v2.8 裏で構築・本番運用時のみ呼ぶ）。

        Args:
            ticker: 4 桁数字（JP）or "AAPL" 形式（US）
            shares: 単元株単位の株数
            side: "buy" / "sell"
            market: "JP" / "US"
            order_type: "MARKET" / "LIMIT"
            trd_pwd: 取引パスワード（必須・settings から渡す）

        Returns:
            {
                "ok": bool,
                "order_id": str | None,
                "fill_price": float | None,
                "status": str,  # "FILLED" / "SUBMITTED" / "REJECTED" / "ERROR"
                "error": str | None,
            }

        Notes:
            実 moomoo 発注を行う。WILLE_BROKER_LIVE=1 + 接続確認後のみ呼ぶこと。
            unlock_trade（取引パスワード認証）→ place_order → 約定確認の順。
        """
        result: dict[str, object] = {
            "ok": False,
            "order_id": None,
            "fill_price": None,
            "status": "ERROR",
            "error": None,
        }
        if shares <= 0:
            result["error"] = "shares must be > 0"
            return result
        if not trd_pwd:
            result["error"] = "trd_pwd is required for real order"
            return result

        try:
            sdk = _import_sdk()
        except BrokerUnavailable as exc:
            result["error"] = f"SDK unavailable: {exc}"
            return result

        try:
            ctx = self._open_ctx(sdk, market)
        except BrokerUnavailable as exc:
            result["error"] = f"OpenD connection failed: {exc}"
            return result

        try:
            # 1. 取引パスワード認証
            ret_unlock, msg_unlock = ctx.unlock_trade(trd_pwd, is_unlock=True)
            if ret_unlock != sdk.RET_OK:
                result["error"] = f"unlock_trade failed: {msg_unlock}"
                return result

            # 2. 銘柄コード整形
            if market == "JP":
                code = f"JP.{ticker}"
            else:
                code = f"US.{ticker}"

            # 3. 発注
            trd_side = sdk.TrdSide.BUY if side.lower() == "buy" else sdk.TrdSide.SELL
            order_type_enum = (
                sdk.OrderType.MARKET if order_type == "MARKET" else sdk.OrderType.NORMAL
            )
            ret, data = ctx.place_order(
                price=0.0 if order_type == "MARKET" else 0.0,  # MARKET なら 0
                qty=shares,
                code=code,
                trd_side=trd_side,
                order_type=order_type_enum,
                trd_env=getattr(sdk.TrdEnv, self._trd_env),
            )
            if ret != sdk.RET_OK:
                result["error"] = f"place_order failed: {data}"
                result["status"] = "REJECTED"
                return result

            # 4. 結果取得
            row = data.iloc[0] if hasattr(data, "iloc") else None
            if row is not None:
                result["order_id"] = str(row.get("order_id", ""))
                price = _f(row.get("price"))
                result["fill_price"] = price
                # MARKET ORDER は即時 SUBMITTED → 約定確認は別途
                result["status"] = "SUBMITTED"
                result["ok"] = True
        except Exception as exc:
            result["error"] = f"place_order exception: {exc}"
        finally:
            try:
                ctx.close()
            except Exception:
                pass
        return result

    def _parse(self, data: Any) -> list[Position]:
        out: list[Position] = []
        for _, row in data.iterrows():
            code = str(row.get("code", "")).split(".")[-1]  # "US.AAPL" -> "AAPL"
            out.append(
                Position(
                    code=code,
                    qty=_f(row.get("qty")) or 0.0,
                    cost_price=_f(row.get("cost_price")) or 0.0,
                    currency=str(row.get("currency", "")),
                    nominal_price=_f(row.get("nominal_price")),
                    market_val=_f(row.get("market_val")),
                    pl_ratio=_f(row.get("pl_ratio")),
                    pl_val=_f(row.get("pl_val")),
                )
            )
        return out
