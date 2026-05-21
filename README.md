# Trading Agent

moomoo OpenAPI を介して米国株・日本株の中期投資を AI マルチエージェントが支援する、
ローカル完結型の個人向けトレードアシスタント（macOS 専用 / Phase 1）。

朝5分のダッシュボード確認で「今日すべきこと」（売り＝利確・損切り／買い）を、
収集トピックス（判断根拠）込みで提示する。

## 現在のステータス

**Phase 1.0：環境構築**（実装着手中）。詳細な進捗は [docs/PROGRESS.md](docs/PROGRESS.md)。

## クイックスタート（開発）

前提：macOS、[uv](https://docs.astral.sh/uv/)、Python 3.12（uv が自動取得）。

```bash
# 依存インストール + 仮想環境作成
uv sync

# 環境変数テンプレートをコピーして編集
cp .env.example .env
$EDITOR .env

# DB 初期化（空の SQLite を ~/.trading-agent/ に作成）
uv run python scripts/init_db.py

# テスト
uv run pytest
```

## セットアップ全体（人間作業を含む）

口座開設・API キー取得・Ollama・moomoo OpenD などの手順は
[docs/OPERATIONS.md](docs/OPERATIONS.md) セクション 1 を参照。

## ドキュメント

設計書一式は [docs/](docs/) にある。実装の起点は以下：

| ファイル | 役割 |
|---|---|
| [docs/CLAUDE_CODE_INSTRUCTIONS.md](docs/CLAUDE_CODE_INSTRUCTIONS.md) | 実装マスター指示書（原則・規約） |
| [docs/IMPLEMENTATION_PHASES.md](docs/IMPLEMENTATION_PHASES.md) | フェーズ別タスクリスト |
| [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | データモデル・MCP・設定 |
| [docs/STEP_A_FINAL.md](docs/STEP_A_FINAL.md) | 要件定義 |

## アーキテクチャ概要

`FastAPI`（MCP ホスト + スケジューラ + UI 配信）を中心に、自作 MCP ツール層が
moomoo / 外部 API / LLM へのアクセスを抽象化し、マルチエージェントが
朝バッチ（JST 5:00）で売り買いレコメンドとトピックスを生成する。
永続化は SQLite（SQLModel）。LLM は Hot/Cold/Critical の3段ルーティング
（Claude Sonnet / Ollama / Claude Opus）でコストを月¥5,000以内に抑える。
