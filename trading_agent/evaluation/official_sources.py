"""公式約定ソース（観測台帳の単一真実源）— L3 substrate。

forward_diagnosis / feedback / gate（公式集合・gate⑥・unlock）が「どの fill 経路を
公式実績に数えるか」を判定する唯一の定義。旧来は同じタプルが 3 箇所
（reporting/forward_diagnosis.py / reporting/feedback.py / evaluation/gate.py）で
重複定義されており、片方だけ更新すると観測台帳がズレる危険があった。集約する
（master/codex L3・値は不変）。

`OFFICIAL_FILL_SOURCES` に含まれる `filled_via` のみが forward 早期診断・edge 評価・
gate⑥・unlock の「公式集合」に入る。それ以外（paper_auto 等）はこれらの計測から
除外される。

**paper_auto を意図的に除外する理由（二重計上回避・codex 条件③）**
notify / auto_fill_paper が立てる `filled_via="paper_auto"` は「ユーザーが推奨通りに
買った想定」のシミュレーション fill。Phase C unlock 有効時は公式フロー
（dummy-system の ds_dispatch）が実 fill を担い、paper_auto は skip される。両者を
公式集合に混ぜると同一ポジションを二重計上するため、公式実績は
`ds_dispatch`（DS A+ dispatch・paper の system edge 検証）と
`manual`（実弾代行・live の実運用実績）に限定する。

**新しい fill 経路を追加するときの規約（重要・L3 の主旨）**
新たな `filled_via` ラベルの fill 経路を足したら、それを公式実績に数えるべきかを
判断する。数えるなら **この `OFFICIAL_FILL_SOURCES` に追加**し、同時に
`tests/unit/test_official_sources.py` の回帰テスト（非公式ラベルが forward/feedback/
gate から除外されることを固定）を更新すること。ここを更新しないと、新経路の実績が
観測台帳から silently drop される。
"""

from __future__ import annotations

# A7: 公式実績に数える約定経路。値は不変（('ds_dispatch','manual')）。
OFFICIAL_FILL_SOURCES: tuple[str, ...] = ("ds_dispatch", "manual")

__all__ = ["OFFICIAL_FILL_SOURCES"]
