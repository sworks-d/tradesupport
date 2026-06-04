"""decisions テーブル（レコメンド + 採否 + 評価の履歴）。SYSTEM_DESIGN.md §2.3。

A-3（2026-05-23・方針確定済）：ver1形（総合スコア前提・全項目必須）を MAGI に適合させる。
- **既存カラムは1つも消さない**（後段の貫通を優先。表分割はPhase2以降）。
- **追加**：`status`（候補→検証→決裁のライフサイクル）／`gendo_stance`（碇の構え）／`verified_at`。
- **nullable化**：候補生成時（status="verifying"）は未確定の予測値を None 許容に
  （score / expected_return / target_period_days / thesis_at_decision / evaluation_date）。
  ※ `score` は残すが UI に総合点としては出さない（D-06）。A/B 育成用データとして保持。
- 予測由来の値（scenarios 等）は別表に出さず、UI で「未照合」扱い（場所でなくフラグで分離）。
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow

# decision のライフサイクル（A-4 がこの遷移で MAGI を貫通させる）
DECISION_STATUSES = (
    "verifying",  # MAGI候補として生成（3審判→防御→統合→碇 を回す前/最中）
    "verified",  # MAGI検証完了（judge_verdict×3 / split / verification / commander_rec 紐付け済）
    "awaiting",  # 人間の決裁待ち
    "approved",  # 採用
    "denied",  # 却下
    "held",  # 保留（防御層 default_hold 等）
    "order_listed",  # 発注リスト入り
    "ordered",  # 発注済
    "filled",  # 約定済（paper auto fill / mark_filled / 朝バッチ notify。評価対象＝_EVALUABLE）
    "holding",  # 保有中
)


class Decision(SQLModel, table=True):
    """1つのレコメンドとその後の追跡（採否・評価）。"""

    __tablename__ = "decisions"

    id: int | None = Field(default=None, primary_key=True)

    # 何のレコメンドか
    date: dt.date = Field(index=True)
    ticker: str = Field(index=True)
    action: str  # "buy" / "sell_profit" / "sell_loss"
    source_signal_id: int | None = None  # buy_signals.id or sell_signals.id

    # MAGI ライフサイクル（A-3 追加）
    status: str = Field(default="verifying", index=True)  # DECISION_STATUSES
    gendo_stance: str | None = None  # 推し/利確/撤退/要検討/静観（碇/割れから導出）
    verified_at: dt.datetime | None = None  # MAGI検証が完了した時点

    # レコメンド時のスナップショット（verifying では未確定のため nullable／A-3）
    score: int | None = None  # UIに総合点としては出さない（D-06）。A/B育成用データ
    expected_return: float | None = None
    target_period_days: int | None = None
    scenarios: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    thesis_at_decision: str | None = None

    # 発注時の記録（P6評価の前提＝R-multiple算定に entry と stop が要る）
    entry_price: float | None = None  # エントリー価格（複数 fill 時は加重平均・v2.1 TASK-E2）
    stop_pct: float | None = None  # 損切り幅（R-mult＝実リターン÷stop・v2.1 で正値統一 TASK-SZ4）
    shares_filled: float = 0.0  # 累積約定株数（複数 fill の平均化に使う・v2.1 TASK-E2）

    # ユーザーの反応
    user_action: str | None = None  # "adopted" / "skipped" / "modified" / "deferred"
    user_acted_at: dt.datetime | None = None
    user_note: str | None = None

    # 評価（評価期日は target_period_days 確定後に決まるため nullable／A-3）
    evaluation_date: dt.date | None = None  # date + target_period_days
    actual_return: float | None = None
    benchmark_return: float | None = None  # 同期間ベンチマーク（S&P比超過の算定用・P6）
    hit_or_miss: str = Field(default="pending", index=True)  # "hit"/"miss"/"neutral"/"pending"
    evaluated_at: dt.datetime | None = None
    # A3/A8: エントリ（約定）時点の市場局面（ゲート⑥「両局面通過」判定用）。
    # A8 で detect_market_cycle() の trailing 局面 "bull"/"bear"/"sideways"/"unknown" を保存。
    # （A3 当時は detect_market_regime_live() の単日 "risk_on"/"risk_off"。gate は両方を後方互換マップ）
    entry_market_regime: str | None = None
    # A7: 監査・公式集合識別。
    # filled_via: 約定経路 "ds_dispatch"（DS 公式）/ "manual"（実弾代行）/ "paper_auto"（試験シミュ）。
    #            official_gate_evaluation は DS 公式由来だけを公式実績に数えられる。
    # entry_date: 実約定日（benchmark 起点に使う。d.date と乖離する遅延 fill で α 歪みを防ぐ）。
    filled_via: str | None = None
    entry_date: dt.date | None = None
    # 公式集合の broker_mode 識別（paper=システム edge 検証 / live=実運用実績）。
    # filled_via=manual は live 専用ではない（mark_filled は paper/manual もある）ため、
    # gate⑥/昇格は filled_via だけでなく broker_mode で分離集計する必要がある（混在汚染防止）。
    # entry（fill）時点で刻む。既存値は尊重（上書きしない）。
    entry_broker_mode: str | None = None

    # 紐付いたトピックス
    supporting_topic_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    # ペーパー検証：どの性格が auto-approve / 紙約定したか（複数 fill 可）。
    # JSON 配列で蓄積（["defender", "aggressor"] 等）。空＝未約定。
    personalities_filled: list[str] = Field(
        default_factory=list, sa_column=Column(JSON)
    )

    created_at: dt.datetime = Field(default_factory=utcnow)
