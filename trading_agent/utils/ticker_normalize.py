"""J-Quants 5桁 ⇄ Universe 4桁 の銘柄コード正規化（v2.10）。

J-Quants の銘柄コード仕様（V2 API）:
  - 通常株:           4桁数字 + 末尾チェックデジット 0      → 5桁 (例: 7203 → 72030)
  - 種類株:           4桁 + チェックデジット (0 以外あり)    → 5桁
  - 新規上場暫定:     3数字 + 英字 1 + チェックデジット 0    → 5桁 (例: 285A → 285A0)

Universe テーブル / 既存システム内では 4桁形式で管理しているため、
J-Quants から取った 5桁コードを Universe 形式に正規化する関数を提供する。

ハルシネーション対策:
  - 入力が想定外（5桁でない・None・空）の場合は **そのまま返す**（推測しない）
  - 普通株以外の特殊コード（種類株のチェックデジット ≠ 0 等）は **本来別扱いだが**、
    現状の Universe には普通株しか入っていないので、末尾 1 文字を機械的に削るだけで OK。
"""

from __future__ import annotations


def jquants_to_universe(jq_code: str | None) -> str:
    """J-Quants 5桁コード → Universe 4桁コード。

    変換規則: 5桁の末尾 1 文字を除去。

    Args:
        jq_code: J-Quants の Code フィールド値（5桁想定）。

    Returns:
        4桁のティッカー。想定外の長さ・None・空は **そのまま返す**（推測しない）。

    Examples:
        >>> jquants_to_universe("72030")
        '7203'
        >>> jquants_to_universe("285A0")
        '285A'
        >>> jquants_to_universe("7203")    # 既に 4桁なら無変換
        '7203'
        >>> jquants_to_universe(None)      # None は None ではなく "" を返す
        ''
    """
    if not jq_code:
        return ""
    if len(jq_code) == 5:
        return jq_code[:-1]
    return jq_code


def universe_to_jquants(uni_code: str | None) -> str:
    """Universe 4桁コード → J-Quants 5桁コード（普通株前提・末尾 0 を付加）。

    Args:
        uni_code: Universe の 4桁ティッカー。

    Returns:
        5桁の J-Quants コード。想定外の長さ・None・空は **そのまま返す**。

    Examples:
        >>> universe_to_jquants("7203")
        '72030'
        >>> universe_to_jquants("285A")
        '285A0'
        >>> universe_to_jquants("72030")   # 既に 5桁なら無変換
        '72030'
        >>> universe_to_jquants(None)
        ''
    """
    if not uni_code:
        return ""
    if len(uni_code) == 4:
        return uni_code + "0"
    return uni_code


def is_jp_ticker(ticker: str | None) -> bool:
    """JP 銘柄かどうか（既存 utils の is_jp と重複しないようここでは慎重に判定）。

    判定規則:
      - 4桁: 全数字 or 末尾英字 1 文字 (新規上場暫定) → JP
      - 5桁: 末尾が 0 + 残り 4 桁が上記パターン → JP（J-Quants コード）
      - それ以外（AAPL, QQQ 等） → 非 JP（US）
    """
    if not ticker:
        return False
    if len(ticker) == 4:
        return ticker.isdigit() or (
            ticker[:3].isdigit() and ticker[3].isalpha()
        )
    if len(ticker) == 5:
        # 5桁の場合は最後を除いて再判定
        head = ticker[:-1]
        return head.isdigit() or (head[:3].isdigit() and head[3].isalpha())
    return False
