# Trading Agent — エージェント設計書

最終更新：2026-05-22
ステータス：**STEP C-3 確定版**

このファイルは Trading Agent の **マルチエージェント** の各エージェントを定義する。
役割、入出力スキーマ、使用する MCP ツール、LLM、プロンプト構造、Phase 1 でのスコープを含む。

関連ドキュメント：
- PANEL_SPECS.md：パネル別ロジック（C-1）
- SYSTEM_DESIGN.md：データモデル・MCPツール（C-2）
- ORCHESTRATION.md：実行順序・統合（C-4）

---

# 📋 朝の確認用：C-3 で踏み込んだ判断

## A. 大きな判断

### A-1. エージェントは LangChain ベース
- 候補：LangChain / 自作 / LlamaIndex / Crew
- **採用：LangChain**（理由：エコシステム、Anthropic 公式サポート、コールバックでコスト記録が楽）
- 懸念：将来の変更で破壊的変更があれば、薄いラッパーで吸収

### A-2. 各エージェントは Tool-using Agent パターン
- LLM がツール（MCP）を呼び出す形式
- ZeroShotReActAgent ではなく、構造化された出力（structured_output）を強制
- これにより「LLM の自由演技」を防ぎ、決定論的な動作を確保

### A-3. プロンプトは XML タグで構造化
- Claude のドキュメント推奨に従う
- system + user の二段構成
- 出力形式は JSON Schema で強制

### A-4. エージェント間の通信は「データベース経由」
- メッセージパッシングではなく、テーブルへの書き込み/読み込み
- 理由：再実行可能、デバッグ容易、状態が見える
- 例：screening_agent が screening_results に書き、market_analyst が読む

## B. Phase 1 で実装する6エージェント

1. **screening-agent** — 銘柄スクリーニング（V字・テーマ）
2. **market-analyst** — 個別銘柄の詳細分析
3. **sell-recommender** — 売り推奨判定
4. **portfolio-builder** — 初回ポートフォリオ構築
5. **manual-input-analyst** — ユーザー投入の即時解釈
6. **topics-collector** — ニュース収集・分類

Phase 2- で position-monitor、strategy-coordinator を追加。

## C. 大きな未決事項

### C-1. プロンプトの細部は Claude Code に委ねる
このファイルでは「**何を入れて、何を出させるか**」を定義。
「具体的にどう書くか」は Claude Code の実装フェーズで実物を見ながら調整。
理由：プロンプトは試行錯誤前提、設計書で完璧を狙うと逆効果。

### C-2. エージェントの並列度は ORCHESTRATION.md で決める
このファイルでは「単体としての挙動」のみ定義。

---

# 0. 全エージェント共通の設計

## 0.1 共通インターフェース

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

class AgentInput(BaseModel):
    """エージェント入力の基底"""
    invocation_id: str  # ロギング用
    dry_run: bool = False  # True なら結果を DB に書かない

class AgentOutput(BaseModel):
    """エージェント出力の基底"""
    success: bool
    invocation_id: str
    error: Optional[str] = None
    summary: str  # 人間向けサマリー
    duration_ms: int
    llm_cost_jpy: float

class Agent(ABC):
    """全エージェントの基底クラス"""

    name: str
    description: str
    input_schema: type[AgentInput]
    output_schema: type[AgentOutput]

    # 使用する MCP ツール
    required_tools: List[str]

    # デフォルトの LLM ルーティング
    default_routing: str  # "hot" / "cold" / "critical"

    @abstractmethod
    async def execute(self, input: AgentInput) -> AgentOutput:
        pass

    async def pre_check(self) -> bool:
        """実行前チェック（必要なツールが使えるか等）"""
        return True
```

## 0.2 共通の挙動

- **冪等性**：同じ入力で同じ結果を返す（LLM の温度0、ランダム性を排除）
- **トレーサビリティ**：全実行を analysis_logs に記録、invocation_id で追跡可
- **コスト記録**：全 LLM 呼び出しを cost_logs に記録
- **エラー処理**：例外を catch、失敗時も AgentOutput を返す（success=False）

## 0.3 プロンプトのテンプレート

```xml
<system>
あなたは [役割] です。
[このエージェントの目的]

利用可能なツール:
- tool_name_1: 説明
- tool_name_2: 説明

出力形式:
JSON 形式で、以下のスキーマに従って出力してください。
[JSON Schema]

重要:
- [守るべきルール]
- [絶対にやってはいけないこと]
</system>

<user>
<context>
[現在の状況、保有銘柄、市場情報など]
</context>

<input>
[実際のタスク]
</input>

<output_constraints>
- 日本語で出力
- 数値は具体的に
- 推測は明示的に「推測」と書く
</output_constraints>
</user>
```

---

# 1. screening-agent

## 1.1 役割

毎朝、**銘柄母集団（500-700社）から候補20件程度** を絞り込む。

V字回復スコア / テーマスコアを計算し、買い候補の入り口を作る。

## 1.2 入出力

### Input

```python
class ScreeningInput(AgentInput):
    universe_size: int = 500
    strategies: List[str] = ["v_shape", "theme"]
    min_score: float = 50.0  # この未満は除外
    max_results: int = 30  # 上位 N 件返す
```

### Output

```python
class ScreeningOutput(AgentOutput):
    candidates: List[ScreeningCandidate]
    total_screened: int
    strategy_breakdown: Dict[str, int]  # 各戦略でヒットした件数

class ScreeningCandidate(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    composite_score: float  # 0-100
    matched_strategies: List[str]
    v_shape_details: Optional[dict]
    theme_details: Optional[dict]
    current_price: float
    market_cap: float
```

## 1.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| screening | V字 / テーマスコアを計算 |
| market_data | 候補銘柄の最新価格 |
| llm_call | テーマ判定の補助（Cold Path） |

**LLM**：Cold（Ollama）中心。テーマキーワードのマッチング判定のみ Hot にエスカレーション。

## 1.4 処理フロー

```
1. settings から universe_size を取得 → universe テーブルから上位 N 件取得
   （時価総額 + 流動性でフィルタ）

2. 各銘柄について並列で V字回復スコアを計算
   - 業績反転（赤字→黒字、減益→増益）
   - 株価底打ち反転（過去 N 日の最安値からの反発率）
   - RSI 40 以下からの反転
   - 出来高の増加

3. 各銘柄について並列でテーマスコアを計算
   - settings.theme_keywords とニュース／IRの一致度
   - セクター × 直近イベント
   - LLM でテーマ該当性を判定（曖昧な場合のみ Hot Path）

4. 各銘柄について composite_score = max(v_shape, theme) を計算

5. 上位 N 件を返す（最低スコアでフィルタ）

6. screening_results テーブルに永続化
```

## 1.5 V字回復スコアの算出

```python
def calculate_v_shape_score(ticker, data) -> float:
    score = 0.0

    # 1. 業績反転 (40 pt)
    if 直近Q EPS > 0 and 前々Q EPS < 0:
        score += 25  # 赤字→黒字
    elif 直近Q EPS成長率 > 0.2 and 前々Q EPS成長率 < 0:
        score += 20  # 減益→大幅増益
    elif 直近Q 売上成長率 > 0.1:
        score += 10

    # 2. 株価底打ち (30 pt)
    drawdown = (current_price - min_price_90d) / min_price_90d
    if drawdown > 0.1 and current_price < max_price_90d * 0.85:
        score += 30  # 安値から 10% 以上反発、ピークからまだ 15% 余地

    # 3. テクニカル (20 pt)
    if 30 < RSI < 50:
        score += 10  # 売られすぎから反転
    if MACD クロス（直近 5 日）:
        score += 10

    # 4. 出来高 (10 pt)
    if 直近5日 出来高 > 30日平均 × 1.5:
        score += 10  # 注目が集まっている

    return min(score, 100)
```

【たたき台】重みは仮設定。Phase 1 で実績を見て調整。

## 1.6 テーマスコアの算出

```python
def calculate_theme_score(ticker, data) -> float:
    score = 0.0

    # 1. テーマキーワード一致 (40 pt)
    # 直近 30 日のニュース / IR に theme_keywords が含まれる割合
    matches = count_keyword_matches(news_30d, theme_keywords)
    score += min(matches * 5, 40)

    # 2. セクター強度 (30 pt)
    # 同セクターが市場全体より上昇しているか
    sector_outperformance = sector_return_30d - market_return_30d
    score += min(sector_outperformance * 100, 30)

    # 3. 機関投資家の動き (20 pt)
    # 直近の大量保有報告書、空売り残高の変化
    score += institutional_activity_score(ticker)

    # 4. LLM 評価 (10 pt)
    # 「この銘柄は現在のテーマトレンドに合致するか」をLLMが判定
    score += llm_theme_alignment(ticker, theme_keywords)

    return min(score, 100)
```

## 1.7 プロンプト（テーマ判定用）

```xml
<system>
あなたは投資テーマ分析の専門家です。

与えられた銘柄と現在のテーマキーワードを照らし合わせ、
銘柄がテーマにどれだけ合致するかを 0-10 で評価してください。

評価基準:
- 10: テーマの中核企業
- 7-9: テーマの主要受益者
- 4-6: 間接的な関連
- 1-3: 一部関連
- 0: 関連なし

出力形式:
{
  "score": <int 0-10>,
  "reasoning": "<簡潔な理由>",
  "is_core_player": <bool>
}
</system>

<user>
<ticker>NVDA</ticker>
<name>NVIDIA Corporation</name>
<sector>半導体</sector>
<themes>AI, 半導体, データセンター</themes>
<recent_business_summary>
データセンター向け GPU で AI 学習・推論市場をリード。
直近Q売上 +427%。CUDA エコシステムで競合に対する参入障壁。
</recent_business_summary>

このテーマ合致度を評価してください。
</user>
```

## 1.8 エッジケース

- **データなし**：universe テーブルが空 → 起動エラー
- **API 失敗**：fundamentals 取得失敗の銘柄は除外、ログに記録
- **全銘柄が低スコア**：上位 5 件のみ返す（最低 min_score を緩和）
- **同点が大量**：composite_score 同点なら market_cap 大きい順

## 1.9 Phase 1 でのスコープ

### 実装
- V字回復スコア（4軸）
- テーマスコア（4軸）
- universe ベースのスクリーニング

### Phase 2- に回す
- ユーザーカスタム戦略
- セクター内 vs セクター間の絶対/相対評価
- 過去のスクリーニング結果からの学習（バックテスト）

---

# 2. market-analyst

## 2.1 役割

screening-agent が出した候補銘柄を **個別に深掘り分析** し、**買いシグナル** を生成。

5軸スコア（ファンダ・テクニカル・ニュース・戦略・AI確信度）と、3シナリオを出力。

## 2.2 入出力

### Input

```python
class MarketAnalystInput(AgentInput):
    tickers: List[str]  # 分析対象（screening の結果）
    parallel: bool = True  # 並列実行
    deep_dive: bool = False  # 詳細分析モード（Critical LLM 使用）
```

### Output

```python
class MarketAnalystOutput(AgentOutput):
    signals: List[BuySignalData]
    skipped: List[dict]  # 分析できなかった銘柄と理由

class BuySignalData(BaseModel):
    ticker: str
    score: int  # 0-100
    confidence: float
    expected_return: float
    win_rate: float
    target_period_days: int
    target_price: float
    entry_price: float
    stop_loss_price: float
    strategy_category: str
    thesis_checklist: List[dict]
    reasons: List[dict]
    risks: List[dict]
    scenarios: List[dict]
    recommended_amount_jpy: int
    # 5軸スコア
    fundamental_score: float
    technical_score: float
    news_sentiment_score: float
    strategy_fit_score: float
    ai_confidence: float
```

## 2.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| market_data | 最新価格、出来高 |
| fundamentals | EPS、PER、PBR、業績推移 |
| technicals | RSI、MACD、トレンド |
| news | 直近7日のニュース |
| disclosure | 直近の適時開示 |
| llm_call | 統合判断、シナリオ生成 |

**LLM**：Hot Path（Sonnet）中心。deep_dive=True の場合 Critical（Opus）。

## 2.4 処理フロー（1銘柄あたり）

```
1. fundamentals tool で財務データ取得
2. technicals tool でテクニカル指標
3. news tool で直近 7 日のニュース
4. disclosure tool で適時開示

5. 5軸のスコアを並列計算:
   - fundamental_score: PER水準、EPS成長、業界平均比較
   - technical_score: トレンド、RSI、出来高
   - news_sentiment_score: ニュースのポジネガ集計
   - strategy_fit_score: screening_results の composite_score
   - ai_confidence: LLM が総合確信度を出す

6. LLM で 3 シナリオ生成（強気/ベース/弱気）

7. LLM で thesis_checklist 生成（3-5項目）

8. LLM で reasons / risks 生成（各3項目以上）

9. 推奨エントリー価格、損切り価格を計算
   - エントリー: 戦略カテゴリに応じて指値計算
   - 損切り: 戦略カテゴリのデフォルト + 直近サポートライン

10. 推奨投資額を計算（max_position_pct_of_cash 等を考慮）

11. buy_signals テーブルに永続化
```

## 2.5 5軸スコアの詳細

### fundamental_score（0-100）

```python
def calculate_fundamental_score(data) -> float:
    score = 0.0

    # PER vs 業界平均 (25pt)
    if PER < 業界平均 * 0.8:
        score += 25  # 大幅割安
    elif PER < 業界平均 * 1.0:
        score += 15
    elif PER < 業界平均 * 1.5:
        score += 5

    # EPS 成長率 (30pt)
    if EPS_growth_yoy > 0.3:
        score += 30
    elif EPS_growth_yoy > 0.15:
        score += 20
    elif EPS_growth_yoy > 0.05:
        score += 10

    # 売上成長率 (20pt)
    if revenue_growth > 0.2:
        score += 20
    elif revenue_growth > 0.1:
        score += 10

    # ROE / ROIC (15pt)
    if ROE > 0.15:
        score += 15
    elif ROE > 0.08:
        score += 8

    # 財務健全性 (10pt)
    if D/E < 0.5 and current_ratio > 1.5:
        score += 10
    elif D/E < 1.0:
        score += 5

    return min(score, 100)
```

### technical_score（0-100）

```python
def calculate_technical_score(data) -> float:
    score = 0.0

    # トレンド (40pt)
    if price > sma_20 > sma_60 > sma_200:
        score += 40  # 強い上昇トレンド
    elif price > sma_20 > sma_60:
        score += 25
    elif price > sma_20:
        score += 10

    # RSI (20pt)
    if 40 < RSI < 60:
        score += 20  # 健全
    elif 30 < RSI < 70:
        score += 10

    # MACD (20pt)
    if MACD_signal_cross_recent and MACD > 0:
        score += 20

    # 出来高 (20pt)
    if volume_5d_avg > volume_30d_avg * 1.2:
        score += 20

    return min(score, 100)
```

### news_sentiment_score（0-100）

```python
def calculate_news_sentiment_score(news_7d) -> float:
    if not news_7d:
        return 50  # ニュースなし = 中立

    sentiments = [analyze_sentiment(article) for article in news_7d]
    # AI 評価で -1 (negative) ~ +1 (positive)

    avg_sentiment = sum(sentiments) / len(sentiments)
    # スコア化: -1 → 0, 0 → 50, +1 → 100
    return (avg_sentiment + 1) * 50
```

### strategy_fit_score（0-100）

```python
# screening_results.composite_score をそのまま採用
return screening_results[ticker].composite_score
```

### ai_confidence（0-100）

LLM が総合判断で出す確信度（後述のプロンプト参照）。

## 2.6 プロンプト（統合判断・シナリオ生成）

```xml
<system>
あなたは中期投資（数ヶ月〜1年）に特化した投資アナリストです。

入力された銘柄について、以下を生成してください:
1. 投資仮説（thesis_checklist: 3-5項目の達成条件）
2. 買うべき理由（reasons: 3項目以上）
3. リスク要因（risks: 3項目以上）
4. 3シナリオ（強気/ベース/弱気）
5. 総合確信度（0-100）

判断原則:
- 確証バイアスを避ける
- 短期の値動きより仮説の確からしさを重視
- 各シナリオは具体的な目標株価と確率を含む
- 確率は合計100%

出力形式（JSON）:
{
  "thesis_checklist": [
    {"item": "...", "rationale": "..."},
    ...
  ],
  "reasons": [
    {"title": "...", "detail": "...", "evidence": "..."},
    ...
  ],
  "risks": [
    {"title": "...", "detail": "...", "magnitude": "high|medium|low"},
    ...
  ],
  "scenarios": [
    {"type": "bull", "target_price": <num>, "return_pct": <num>, "prob": <num>, "desc": "..."},
    {"type": "base", "target_price": <num>, "return_pct": <num>, "prob": <num>, "desc": "..."},
    {"type": "bear", "target_price": <num>, "return_pct": <num>, "prob": <num>, "desc": "..."}
  ],
  "ai_confidence": <num 0-100>,
  "target_period_days": <int>,
  "strategy_category": "中期" | "長期" | "中期-長期"
}
</system>

<user>
<ticker>NVDA</ticker>
<name>NVIDIA Corporation</name>

<fundamentals>
EPS Q4 2025: $5.16 (前年比 +427%)
PER: 35.2 (業界平均 28.5)
PBR: 28.4
売上成長率: +94% YoY
営業利益率: 62%
ROE: 88%
</fundamentals>

<technicals>
現在価格: $98.50
SMA20: $102, SMA60: $98, SMA200: $85
RSI: 45
MACD: シグナルクロス（5日前）
出来高: 30日平均比 +35%
</technicals>

<recent_news>
1. NVIDIA、Blackwell量産出荷を予定通り開始 (5/19)
2. Microsoft, Google, Meta が大量発注 (5/18)
3. 中国向け規制強化の懸念報道 (5/17)
4. AMD MI325 発表、NVIDIA H200 に迫る性能 (5/15)
</recent_news>

<theme_match>
AI: 中核企業
半導体: 中核企業
データセンター: 受益者
</theme_match>

<screening_score>78</screening_score>

この銘柄の投資仮説、3シナリオ、確信度を生成してください。
</user>
```

## 2.7 推奨数量・指値の決定ロジック

```python
def determine_recommendation(signal_data, cash_jpy, total_assets_jpy) -> dict:
    # 1銘柄あたりの上限
    max_cash_pct = settings.max_position_pct_of_cash  # 0.20
    max_total_pct = settings.max_position_pct_of_total  # 0.10

    max_amount = min(
        cash_jpy * max_cash_pct,
        total_assets_jpy * max_total_pct,
    )

    # スコアによる調整
    if signal_data.score >= 90:
        amount = max_amount * 1.0
    elif signal_data.score >= 80:
        amount = max_amount * 0.8
    elif signal_data.score >= 70:
        amount = max_amount * 0.6
    elif signal_data.score >= 65:
        amount = max_amount * 0.4
    else:
        amount = max_amount * 0.2

    # 株数に変換（最小ロット考慮）
    if signal_data.market == "US":
        qty = max(1, int(amount / current_price_jpy))
    else:  # JP
        qty = max(100, int(amount / current_price_jpy / 100) * 100)

    # エントリー指値
    if signal_data.strategy_category == "中期" and is_v_shape:
        entry_price = current_price * (1 - 0.015)  # -1.5%
    elif signal_data.strategy_category == "中期":
        entry_price = current_price * (1 - 0.005)  # -0.5%
    else:  # 長期
        entry_price = current_price  # 成行相当

    # 損切り
    stop_loss_price = entry_price * (1 + signal_data.stop_loss_pct)

    return {
        "qty": qty,
        "entry_price": entry_price,
        "stop_loss_price": stop_loss_price,
        "recommended_amount_jpy": int(qty * entry_price_jpy),
    }
```

## 2.8 エッジケース

- **fundamentals 取得失敗**：その銘柄をスキップ、reasoning に「データ不足」を記録
- **ニュースゼロ**：news_sentiment_score = 50（中立）
- **3シナリオの確率が合計100%でない**：正規化して再計算
- **LLM の出力が JSON として無効**：1回リトライ → 失敗ならスキップ
- **deep_dive モード**：Critical（Opus）を使用、コストが高いため要承認

## 2.9 Phase 1 でのスコープ

### 実装
- 5軸スコアリング
- 3シナリオ生成
- thesis_checklist
- 推奨数量・指値の自動決定

### Phase 2- に回す
- セクター内相対比較
- 競合企業との比較分析
- 過去の類似ケースとの照合（RAG ベース）
- 並列度の動的調整

---

# 3. sell-recommender

## 3.1 役割

**保有銘柄を毎朝チェック**し、利確 or 損切りを推奨。

## 3.2 入出力

### Input

```python
class SellRecommenderInput(AgentInput):
    tickers: Optional[List[str]] = None  # None なら全保有銘柄
    skip_recently_bought: bool = True  # 買ってから 3日以内は対象外
```

### Output

```python
class SellRecommenderOutput(AgentOutput):
    sell_signals: List[SellSignalData]
    scenario_updates: List[ScenarioUpdate]

class SellSignalData(BaseModel):
    ticker: str
    signal_type: str  # "profit_taking" / "stop_loss"
    score: int
    # 利確の場合
    target_achievement_score: Optional[float]
    scenario_achievement_score: Optional[float]
    technical_warning_score: Optional[float]
    # 損切りの場合
    scenario_break_score: Optional[float]
    loss_magnitude_score: Optional[float]
    negative_news_score: Optional[float]
    # 共通
    ai_confidence: float
    reasons: List[dict]
    risks: List[dict]
    recommended_action: dict

class ScenarioUpdate(BaseModel):
    ticker: str
    scenario_health: float
    scenario_status: str  # "intact" / "weakening" / "broken"
    checklist_progress: List[dict]
```

## 3.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| market_data | 現在価格、含み損益計算 |
| fundamentals | 最新の財務（仮説達成度評価） |
| technicals | 過熱感、頭打ちサイン |
| news | 直近 7 日のネガティブニュース |
| llm_call | thesis_checklist の進捗評価、総合判断 |

**LLM**：Hot Path（Sonnet）

## 3.4 処理フロー

```
1. portfolio から active な銘柄を取得
2. 各銘柄について並列で以下を実行:

   2.1. 含み損益計算
   2.2. 進捗計算（経過日数 / target_period_days）
   2.3. シナリオ進捗評価
        - thesis_checklist の各項目を LLM が評価
        - checked / not_checked / partially_checked
        - 全体の scenario_health（0-1）を算出
   2.4. テクニカル悪化チェック（RSI、出来高、ローソク足）
   2.5. ネガティブニュースチェック

3. signal_type の判定:
   - 含み損益 > 0 → profit_taking 候補として評価
   - 含み損益 < 0 → stop_loss 候補として評価

4. profit_taking_score / stop_loss_score を計算（PANEL_SPECS C-1.2 参照）

5. scenarios テーブルを更新

6. score >= 50 のものを sell_signals テーブルに保存
```

## 3.5 thesis_checklist の評価プロンプト

```xml
<system>
あなたは投資仮説の進捗評価担当です。

入力された銘柄の thesis_checklist の各項目について、
現在の財務データ、ニュース、市場状況を踏まえて、
各項目が達成されているかを評価してください。

評価:
- "checked": 完全に達成
- "partially_checked": 部分的に達成（50%以上）
- "not_checked": 未達成
- "broken": 仮説そのものが崩れた（達成不可能と判明）

出力形式（JSON）:
{
  "checklist_progress": [
    {
      "item": "<元の項目>",
      "status": "checked" | "partially_checked" | "not_checked" | "broken",
      "evidence": "<判断根拠>",
      "confidence": <num 0-1>
    },
    ...
  ],
  "overall_health": <num 0-1>,
  "overall_status": "intact" | "weakening" | "broken",
  "summary": "<1-2文の総合評価>"
}
</system>

<user>
<ticker>TSLA</ticker>
<thesis_checklist>
[
  {"item": "FSD レベル4 が Q3 までに認可される"},
  {"item": "EPS が前年比 +30% 以上"},
  {"item": "中国 EV シェアが維持される"},
  {"item": "サイバートラック生産が計画通り"}
]
</thesis_checklist>

<current_situation>
<fundamentals>
EPS 直近Q: 前年比 +5%（予想 +25% を下回る）
売上: 前年比 -2%
</fundamentals>

<recent_news>
1. FSD 収益化遅延、米運輸省が事故率データを再調査 (5/20)
2. 中国市場で BYD にシェアを奪われる (5/15)
3. サイバートラック生産遅延の報道 (5/10)
</recent_news>

<technicals>
株価: 取得から -7.8%
RSI: 38（売られすぎ寄り）
出来高: 平均比 +50%（売りが集まっている）
</technicals>
</current_situation>

各項目を評価してください。
</user>
```

## 3.6 損切り判定の特別ロジック

損切りスコアが高い場合、**規律のメッセージ** を必ず含める：

```python
if signal_type == "stop_loss" and score >= 70:
    reasons.append({
        "title": "事前に決めた損切りラインに到達",
        "detail": f"取得 {buy_price} に対し損切りライン {stop_loss_pct*100}% = {stop_loss_price}。現在 {current_price} で残り {distance_to_loss_cut:.1%}。ルールを破ると規律が崩壊する。",
        "priority": "規律",
    })
    reasons.append({
        "title": "感情的な判断で傷を広げる典型パターン",
        "detail": "「もう少し待てば戻るかも」が損失拡大の最大要因。機械的にルール通り切ることが規律。",
        "priority": "規律",
    })
```

これは PANEL_SPECS で確定した「FX経験を踏まえた設計」の実装。

## 3.7 推奨売却数量・指値の決定

PANEL_SPECS C-1.2 の section 3.5 / 3.6 に従う：

```python
def determine_sell_recommendation(signal: SellSignalData, portfolio: Portfolio) -> dict:
    if signal.signal_type == "profit_taking":
        if signal.score >= 90:
            action = {"qty": portfolio.qty, "type": "limit", "price": current_price * 0.997}
            qty_label = "全量"
        else:  # 70-89
            action = {"qty": portfolio.qty // 2, "type": "limit", "price": current_price * 0.997}
            qty_label = "半量"
    else:  # stop_loss
        # 常に全量、成行
        action = {"qty": portfolio.qty, "type": "market"}
        qty_label = "全量（成行）"

    return {
        **action,
        "qty_label": qty_label,
        "note": f"{signal_type}のため",
    }
```

## 3.8 エッジケース

- **保有0件**：sell_signals 空配列、scenarios 更新なし
- **買って3日以内**：skip_recently_bought=True ならスキップ
- **thesis_checklist が空**：LLM 評価できない → デフォルトで scenario_health=0.5
- **損切りライン未設定**：デフォルト -8% を適用
- **目標期間超過 + 損益ニュートラル**：「期間切れ」として警告レベルで sell_signals に追加（score 60 程度）

## 3.9 Phase 1 でのスコープ

### 実装
- 利確 / 損切りスコアの算出
- シナリオ進捗評価（LLM ベース）
- 規律メッセージ（損切り時）

### Phase 2- に回す
- 部分売却の動的判定（30%/50%/70%）
- 税金考慮（取得から 1 年経過か等）
- リバランス推奨

---

# 4. portfolio-builder

## 4.1 役割

**初回ポートフォリオ構築**（Stage 1 → Stage 2 への移行時）と、**Core-Satellite 比率の管理**。

## 4.2 入出力

### Input

```python
class PortfolioBuilderInput(AgentInput):
    mode: str = "review"  # "initial" / "review" / "rebalance"
    available_cash_jpy: int
    candidates: List[str]  # buy_signals から渡された候補
    target_allocation: dict  # {"core": 0.8, "satellite": 0.2}
```

### Output

```python
class PortfolioBuilderOutput(AgentOutput):
    recommendations: List[PortfolioRecommendation]
    rebalance_actions: List[dict]  # mode == "rebalance" のみ

class PortfolioRecommendation(BaseModel):
    ticker: str
    action: str  # "buy_new" / "add_position" / "reduce_position" / "exit"
    rationale: str
    target_allocation_pct: float
    target_amount_jpy: int
```

## 4.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| market_data | 現在価格 |
| llm_call | 配分の戦略的判断 |

**LLM**：Hot Path（Sonnet）

## 4.4 処理フロー

### initial モード（Stage 1 → Stage 2）

```
1. 現金残高を確認
2. buy_signals からスコア順に候補をピックアップ
3. 各候補について:
   - Core / Satellite を判定（strategy_category）
   - 配分比率を決定（target_allocation を尊重）
4. 推奨ポートフォリオを生成（5-7銘柄）
```

### review モード（毎朝バッチ）

```
1. 現在のポートフォリオを評価
2. Core / Satellite 比率の確認
3. 過度な集中（同セクター 50% 超等）の警告
4. 新規買い候補との整合性チェック
```

### rebalance モード（月次 or トリガー）

```
1. 目標配分との乖離を計算
2. リバランス推奨を生成（売却 + 新規買い）
```

## 4.5 Phase 1 でのスコープ

### 実装
- review モード（毎朝、配分チェック）
- initial モード（初回構築の推奨）

### Phase 2- に回す
- rebalance モード（自動リバランス）
- セクター集中度の動的調整
- リスクパリティ等の高度な配分理論

---

# 5. manual-input-analyst

## 5.1 役割

ユーザーが投入した **X発言・記事URL・テキスト** を即時解釈し、保有銘柄・買い候補への影響を判定。

## 5.2 入出力

### Input

```python
class ManualInputAnalystInput(AgentInput):
    text: str
    url: Optional[str] = None
    user_hint: Optional[dict] = None  # 例: {"tickers": ["NVDA"]}
```

### Output

```python
class ManualInputAnalystOutput(AgentOutput):
    result_summary: str
    affected_tickers: List[str]
    impact_direction: str  # "positive" / "negative" / "neutral" / "mixed"
    impact_magnitude: str  # "large" / "medium" / "small"
    recommended_actions: List[str]
    suggested_topic: Optional[dict]  # トピックス化候補
```

## 5.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| llm_call | 解釈・影響判定 |

**オプション** :
- URL 取得（Phase 1 はテキストのみ、URL は Phase 2-）

**LLM**：Hot Path（Sonnet）

## 5.4 処理フロー

```
1. 入力タイプの判定（text / url / ticker code）
2. (Phase 2-) URL の場合は取得 + テキスト抽出
3. LLM で解釈:
   - 投入内容の要約
   - 影響先銘柄の抽出（保有 + 候補から）
   - 影響の方向と規模
   - 推奨アクション
4. manual_inputs テーブルに保存
5. ユーザーが「トピックスに追加」を選択した場合、topics テーブルに転送
```

## 5.5 プロンプト

```xml
<system>
あなたは即時市場分析の専門家です。

ユーザーが投入した情報（X発言、記事、コメント等）について、
ユーザーの保有銘柄と買い候補にどう影響するかを分析してください。

判断原則:
- 短期の値動きより、中期トレンドへの影響を重視
- 直接的な影響と間接的な影響を区別
- 不確実性が高い場合は「不明確」と明示

出力形式（JSON）:
{
  "result_summary": "<2-3文の要約>",
  "affected_tickers": [<該当銘柄リスト>],
  "ticker_impacts": [
    {
      "ticker": "<>",
      "direction": "positive|negative|neutral",
      "magnitude": "large|medium|small",
      "reasoning": "<>"
    }
  ],
  "overall_direction": "positive|negative|neutral|mixed",
  "overall_magnitude": "large|medium|small",
  "recommended_actions": [<アクション文字列リスト>],
  "confidence": <num 0-1>
}
</system>

<user>
<user_portfolio>
保有: AAPL, MSFT, TSLA, 7203, 6920
買い候補: NVDA, 6920, AVGO, AMD, ASML, 8035, META, 6857, GOOGL, 9984
</user_portfolio>

<input_text>
Powell が利下げに前向き発言、6月FOMCで決定の可能性
</input_text>

<context>
入力ソース: X (Twitter)
投稿時刻: 2026-05-21 05:47
</context>

このユーザーへの影響を分析してください。
</user>
```

## 5.6 エッジケース

- **保有 / 候補に該当なし**：「投資判断に影響しないと判定」を返す
- **テキストが短すぎる（10文字未満）**：処理せずエラー
- **LLM が JSON 構造を壊した**：1回リトライ → 失敗ならテキストとして返す
- **コスト上限超過**：処理拒否、ユーザーに通知

## 5.7 Phase 1 でのスコープ

### 実装
- テキスト解釈
- 影響先銘柄の抽出
- 推奨アクション
- トピックス化候補の生成

### Phase 2- に回す
- URL 自動取得
- 画像 OCR
- 過去の手動投入を学習に使う（Memory）

---

# 6. topics-collector

## 6.1 役割

朝バッチで **ニュース・IR・適時開示を収集**し、重要度分類と影響先紐付けを行う。

## 6.2 入出力

### Input

```python
class TopicsCollectorInput(AgentInput):
    since_hours: int = 24  # 過去 N 時間
    sources: Optional[List[str]] = None  # 特定ソースに絞る
```

### Output

```python
class TopicsCollectorOutput(AgentOutput):
    topics_added: int
    topics_updated: int  # 既存の更新
    by_category: Dict[str, int]
    by_importance: Dict[str, int]
```

## 6.3 使用ツール / LLM

| MCP Tool | 用途 |
|---|---|
| news | NewsAPI / RSS 取得 |
| disclosure | TDnet RSS, EDINET |
| llm_call | 要約、重要度判定、影響先抽出 |

**LLM**：Cold Path（Ollama）中心。重要度が曖昧な場合のみ Hot Path。

## 6.4 処理フロー

```
1. 各ソースから過去 24 時間の記事を取得

2. デデュープ:
   - URL 完全一致
   - headline のハッシュ
   - headline の類似度（Levenshtein）

3. 各記事について並列で:
   3.1. 要約生成（Cold: Ollama）
   3.2. カテゴリ分類（macro / sector / stock）
   3.3. 重要度の一次判定（ルール）
   3.4. 影響先銘柄の抽出（universe + portfolio + buy_candidates から）
   3.5. 影響テキスト生成

4. ルールで判定できない記事のみ、Hot Path で重要度を再評価

5. レコメンドとの紐付け:
   - affected_tickers と一致する decisions を検索
   - linked_decisions と supporting_topics を相互更新

6. topics テーブルに永続化
```

## 6.5 重要度判定のルール

```python
def rule_based_importance(article) -> str:
    # high
    if article.source_type == "SEC_EDGAR" and article.form_type == "8-K":
        return "high"
    if article.source_type == "TDnet" and "決算速報" in article.title:
        return "high"
    if article.contains_keywords(["FOMC", "利上げ", "利下げ", "rate hike", "rate cut"]):
        return "high"
    if any(t in article.affected_tickers for t in portfolio.tickers):
        return "high"  # 保有銘柄に直接影響

    # medium
    if any(t in article.affected_tickers for t in buy_candidates):
        return "medium"
    if article.is_sector_news:
        return "medium"

    # low
    return "low"
```

## 6.6 LLM での重要度補強

```xml
<system>
ニュース記事の重要度を判定してください。

ルールベースで "low" と判定された記事について、
LLM の判断で重要度を見直します。

判断基準:
- ユーザーの保有銘柄に間接的でも影響するか
- 業界全体のトレンドを変えうるか
- マクロ環境（金利、為替、地政学）に影響するか

出力:
{"importance": "high|medium|low", "reasoning": "..."}
</system>

<user>
<article>
タイトル: [タイトル]
要約: [要約]
影響先（候補）: [tickers]
</article>

<user_portfolio>
保有: [tickers]
買い候補: [tickers]
</user_portfolio>

重要度を再評価してください。
</user>
```

## 6.7 影響先銘柄の抽出

```python
async def extract_affected_tickers(article) -> List[str]:
    # 1. 明示的なティッカー（"NVDA", "$NVDA", "(7203)"）を正規表現で抽出
    explicit = extract_tickers_regex(article.text)

    # 2. 企業名 → ティッカーマッピング
    # universe テーブルから企業名を引いてマッチング
    by_name = match_company_names(article.text, universe)

    # 3. LLM で補強（曖昧な場合のみ Cold Path）
    if not explicit and not by_name:
        llm_extracted = await llm.extract_tickers(article.text, universe)
    else:
        llm_extracted = []

    return list(set(explicit + by_name + llm_extracted))
```

## 6.8 影響テキスト生成

```python
async def generate_impact_text(topic, affected_tickers, recommendations) -> str:
    # 各 affected_ticker の状況に応じてテンプレート選択
    impacts = []

    for ticker in affected_tickers:
        if ticker in portfolio_tickers:
            position = portfolio[ticker]
            if position.status == "active":
                # 保有銘柄
                impact = await llm_generate_impact_holding(topic, position)
                impacts.append(impact)
        elif ticker in buy_candidate_tickers:
            # 買い候補
            impact = await llm_generate_impact_candidate(topic, ticker)
            impacts.append(impact)

    return " / ".join(impacts) if impacts else "影響先は明確に特定できませんでした。"
```

例：
- 「**TSLA** **損切り推奨**の根拠の一つ・**シナリオ崩壊**を補強」
- 「**NVDA** **買い推奨**の根拠を強化」

## 6.9 エッジケース

- **API 失敗**：そのソースをスキップ、ログに記録、他は続行
- **記事ゼロ**：「ニュースが少ない日」として正常終了
- **デデュープで全削除**：ログに記録、次回バッチで warning
- **LLM 失敗**：そのトピックの重要度は "low"、影響テキストは「分析失敗」

## 6.10 Phase 1 でのスコープ

### 実装
- 主要ニュースソース（NewsAPI, RSS, TDnet, EDINET）
- 重要度判定（ルール + LLM）
- 影響先銘柄の自動抽出
- 影響テキスト生成

### Phase 2- に回す
- X (Twitter) API 連携（公式コスト次第）
- 動画コンテンツの解析
- 過去ニュースの定期的な再評価
- 影響テキストの動的更新（新ニュース到着時）

---

# 7. 共通：エージェント実行のラッパー

全エージェントは以下のラッパー経由で実行：

```python
async def execute_agent(agent: Agent, input: AgentInput) -> AgentOutput:
    invocation_id = str(uuid4())
    start = time.time()

    # 緊急停止チェック
    if halt_file_exists():
        return AgentOutput(
            success=False,
            invocation_id=invocation_id,
            error="HALT file present, agent execution aborted",
            summary="緊急停止中",
            duration_ms=0,
            llm_cost_jpy=0,
        )

    # 予算チェック（事前見積もり）
    if not await budget.can_proceed_for_agent(agent.name):
        return AgentOutput(
            success=False,
            invocation_id=invocation_id,
            error="Budget exceeded",
            summary="予算超過",
            duration_ms=0,
            llm_cost_jpy=0,
        )

    # 開始ログ
    await log_analysis_start(agent.name, invocation_id, input)

    # 実行
    try:
        output = await agent.execute(input)
        output.invocation_id = invocation_id
        output.duration_ms = (time.time() - start) * 1000
    except Exception as e:
        output = AgentOutput(
            success=False,
            invocation_id=invocation_id,
            error=str(e),
            summary=f"実行失敗: {agent.name}",
            duration_ms=(time.time() - start) * 1000,
            llm_cost_jpy=0,
        )
        log.exception(f"Agent {agent.name} failed", invocation_id=invocation_id)

    # 終了ログ
    await log_analysis_end(invocation_id, output)

    return output
```

---

# 8. 全エージェントの依存関係

```
朝バッチの実行順序:

1. topics-collector
   ↓ (topics テーブル更新)
2. screening-agent
   ↓ (screening_results 更新)
3. market-analyst × N（並列）
   ↓ (buy_signals 更新)
4. sell-recommender
   ↓ (sell_signals + scenarios 更新)
5. portfolio-builder (review モード)
   ↓ (recommendations)

manual-input-analyst は独立、ユーザー投入時に都度実行。
```

詳細は ORCHESTRATION.md（C-4）で確定する。

---

# 9. プロンプト管理

## 9.1 プロンプトの保存

```
trading_agent/agents/prompts/
├── screening/
│   ├── theme_match.xml
│   └── ...
├── market_analyst/
│   ├── analysis.xml
│   ├── scenarios.xml
│   └── ...
├── sell_recommender/
│   ├── thesis_evaluation.xml
│   └── ...
├── manual_input/
│   └── analysis.xml
└── topics_collector/
    ├── importance.xml
    └── impact.xml
```

XML ファイルとして外部化、Python から読み込み + テンプレート展開。

## 9.2 プロンプトのバージョン管理

- ファイル冒頭にバージョン情報（コメント）
- 重要な変更時は git のタグ付け
- 各バージョンでのスコア精度を analysis_logs から逆引きできるよう、prompt_version を記録

---

# 10. テスト戦略（Phase 1）

## 10.1 単体テスト

- スコア算出関数（純粋関数）：100% カバレッジ目標
- 推奨数量計算：境界値テスト

## 10.2 統合テスト

- モック LLM + 実 DB でエージェント実行
- 想定入力 → 期待出力の対応表

## 10.3 ライブテスト

- ペーパー口座で 1 週間の朝バッチ実行
- analysis_logs の網羅性チェック
- コスト集計の正確性

---

# 11. 次のステップ

C-3 で定義した：
- 6エージェントの I/O スキーマ
- 各エージェントの使用ツール / LLM
- 処理フロー
- プロンプト構造（テンプレート）
- スコア算出の詳細ロジック
- エッジケース

次は **C-4：オーケストレーション**で、エージェント間の：
- 実行順序、並列度、依存関係
- メッセージ受け渡し
- エラー時のフォールバック
- 部分失敗時の継続戦略
- 朝バッチ全体のフロー

を確定する。
