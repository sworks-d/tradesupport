# Trading Agent — 運用設計書

最終更新：2026-05-22
ステータス：**STEP C-5 確定版（STEP C 完了）**

このファイルは Trading Agent の **運用フェーズ** を定義する。
セットアップ手順、常駐化、バックアップ、トラブルシューティングを含む。

関連ドキュメント：
- SYSTEM_DESIGN.md：システム基盤（C-2）
- AGENT_SPECS.md：エージェント（C-3）
- ORCHESTRATION.md：実行フロー（C-4）

---

# 📋 朝の確認用：C-5 で踏み込んだ判断

## A. 大きな判断

### A-1. macOS 専用、Docker 化しない
- **採用：macOS ネイティブ実行**（Python + uvicorn + launchd）
- 理由：個人開発、macOS でしか動かさない、Docker は overkill
- 移行性：将来別環境が必要になったら Docker 化、今は不要

### A-2. パッケージ管理は uv
- 候補：pip / poetry / uv / rye
- **採用：uv**（高速、シンプル、pyproject.toml 対応）

### A-3. プロセス管理は launchd
- 候補：launchd / systemd / pm2 / supervisor
- **採用：launchd**（macOS ネイティブ、追加インストール不要）

### A-4. UI は SSR せず、静的ビルドで配信
- Next.js を `next build && next export` で静的化
- FastAPI が静的ファイルを serve
- 理由：個人利用、SSR不要、デプロイ簡単

### A-5. バックアップは手動 + 自動の2層
- 手動：ユーザーが任意のタイミングで実行（重要な投資判断後等）
- 自動：日次で別ディレクトリにコピー（外部ストレージは Phase 2-）

## B. 小さな判断

- B-1. Python 3.12 を推奨（async の最新機能）
- B-2. SQLite ファイルは `~/.trading-agent/db.sqlite`（home directory）
- B-3. ログは `~/.trading-agent/logs/YYYY-MM-DD.log`
- B-4. 設定ファイルは プロジェクトルートの `.env`
- B-5. UI ポートは 8000、変更可能

## C. ユーザー（あなた）の関与ポイント

| フェーズ | 作業 | 所要時間 |
|---|---|---|
| 初回セットアップ | Python・Ollama・moomoo・APIキー | 2-3時間 |
| 日次運用 | ダッシュボード確認、moomooで発注 | 5-15分 |
| 週次振り返り | 週次レポート確認 | 15-30分 |
| 月次レビュー | パラメータ調整、コスト確認 | 30分-1時間 |
| 緊急時対応 | HALT、再起動、トラブルシュート | 状況次第 |

---

# 1. セットアップ手順

## 1.1 前提条件

### 必須
- **macOS 14（Sonoma）以降**
- **Python 3.12 以降**
- **Anthropic API キー**（クレカ登録必要）

### 推奨
- **メモリ 16GB 以上**（Ollama を快適に動かすため）
- **SSD 50GB 以上の空き**（DB + ログ + Ollama モデル）
- **常時インターネット接続**

### オプション
- Slack webhook URL（通知用）

## 1.2 セットアップの全体像

```
1. Python 環境構築
2. プロジェクトクローン
3. 依存パッケージインストール
4. Ollama インストール + モデルダウンロード
5. moomoo 証券口座開設
6. moomoo OpenD インストール
7. 各種 API キー取得
8. .env 設定
9. データベース初期化
10. 初回起動テスト
11. launchd 登録（常駐化）
12. ダッシュボード確認
```

各ステップ、人間（ユーザー）と Claude Code の分担を明示。

## 1.3 ステップ別の詳細

### Step 1: Python 環境構築

**担当：人間**

```bash
# uv インストール（推奨）
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12 を取得
uv python install 3.12
```

**Claude Code の補助**：
- `pyproject.toml` のセットアップ
- 必要なら Python バージョン確認スクリプト

### Step 2: プロジェクトクローン

**担当：人間**

```bash
git clone <repo-url> ~/Projects/trading-agent
cd ~/Projects/trading-agent
```

**Claude Code の補助**：
- リポジトリの作成（Phase 0 で）
- README.md でクローン手順を案内

### Step 3: 依存パッケージインストール

**担当：人間**

```bash
uv sync  # pyproject.toml から仮想環境作成 + 全依存をインストール
```

**Claude Code の補助**：
- `pyproject.toml` の作成・保守
- インストール失敗時のトラブルシュート

主要な依存：
```toml
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]",
    "sqlmodel>=0.0.20",
    "alembic",
    "pydantic>=2.0",
    "pydantic-settings",
    "anthropic>=0.40",
    "langchain>=0.3",
    "langchain-anthropic",
    "langchain-ollama",
    "apscheduler",
    "feedparser",
    "yfinance",
    "ta-lib",  # 別途バイナリインストール必要
    "structlog",
    "tenacity",
    "httpx",
    "moomoo-api",  # moomoo Python SDK
    "jpx-jquants-api-client",  # J-Quants クライアント
]
```

### Step 4: Ollama インストール + モデルダウンロード

**担当：人間**

```bash
# Homebrew でインストール
brew install ollama

# サービス起動
brew services start ollama

# モデルをプル
ollama pull llama3.1:8b
# または日本語特化:
ollama pull schroneko/llama-3-elyza-jp-8b
```

**Claude Code の補助**：
- 推奨モデルの選定アドバイス
- 動作確認スクリプト

### Step 5: moomoo 証券口座開設

**担当：人間（オンラインで完結、約 30 分）**

1. https://www.moomoo.com/jp/ にアクセス
2. 個人情報入力
3. マイナンバー・本人確認書類アップロード
4. 審査（数営業日）
5. 入金（初回 5,000 円程度でも可）

**Claude Code の補助**：なし（人間作業）

### Step 6: moomoo OpenD インストール

**担当：人間（10 分）**

1. moomoo 公式サイトから OpenD アプリをダウンロード（macOS 版）
2. インストール → 起動
3. moomoo 口座でログイン
4. 設定 → API → ローカル接続を有効化
5. ポート 11111 で待機状態を確認

**Claude Code の補助**：
- 接続確認スクリプト

### Step 7: 各種 API キー取得

**担当：人間**

| サービス | 取得方法 | 必須 | コスト |
|---|---|---|---|
| Anthropic API | console.anthropic.com で発行 | ✓ | 従量課金（クレカ必要） |
| NewsAPI | newsapi.org で登録 | △ | 無料（100req/日） |
| J-Quants | jpx-jquants.com で登録 | △ | 無料 |
| EDINET | api.edinet-fsa.go.jp で登録 | △ | 無料 |

**Claude Code の補助**：
- 各サービスの登録手順を README に記載
- API キーの動作確認スクリプト

### Step 8: .env 設定

**担当：人間**

```bash
cp .env.example .env
# エディタで .env を開き、各キーを設定
```

**Claude Code の補助**：
- .env.example の作成・保守
- 必須項目のチェッカー（init_db 時にバリデーション）

### Step 9: データベース初期化

**担当：Claude Code が実装、人間が実行**

```bash
uv run python scripts/init_db.py
```

このスクリプトが：
1. SQLite ファイルを作成（`~/.trading-agent/db.sqlite`）
2. 全テーブルを作成
3. settings テーブルにデフォルト値を投入
4. universe テーブルに初期銘柄リストを投入（時価総額上位 500 社）
5. 動作確認（健康チェック実行）

### Step 10: 初回起動テスト

**担当：人間**

```bash
# 手動起動（フォアグラウンド）
uv run uvicorn trading_agent.main:app --host 127.0.0.1 --port 8000

# ブラウザで http://localhost:8000 を開く
# ダッシュボードが（空でも）表示されることを確認
```

**Claude Code の補助**：
- 起動エラー時のトラブルシュート（よくあるエラーを README に記載）

### Step 11: launchd 登録（常駐化）

**担当：Claude Code が plist 生成、人間が launchctl load**

```bash
# 1. plist 生成
uv run python scripts/setup_launchd.py

# 2. 登録
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist

# 3. 起動確認
launchctl list | grep tradingagent
```

生成される plist：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.tradingagent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/USERNAME/.local/bin/uv</string>
        <string>run</string>
        <string>uvicorn</string>
        <string>trading_agent.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/USERNAME/Projects/trading-agent</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>NetworkState</key>
        <true/>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/USERNAME/.trading-agent/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/USERNAME/.trading-agent/logs/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

### Step 12: ダッシュボード確認

**担当：人間**

ブラウザで http://localhost:8000 を開く。

初回は：
- 保有銘柄なし
- 売り推奨なし
- 買い推奨は朝バッチ後に表示
- トピックスは少なめ

**初回朝バッチ実行**：
```bash
# 手動で朝バッチを実行（待つのが嫌な場合）
curl -X POST http://localhost:8000/api/run_morning_batch
```

5-20 分後にダッシュボードを再読み込み → レコメンドが出る。

---

# 2. 日次運用フロー

## 2.1 朝のレビュー（5-15分）

```
1. ブラウザで http://localhost:8000 を開く
2. ダッシュボードで以下を確認:
   - 売り推奨カード（最優先）
   - 買い推奨カード
   - 警告セクション
   - 保有銘柄の進捗
3. 採用判断:
   - 「採用する」ボタンを押すと decision に記録
   - 詳細パネルで深掘り
4. moomoo アプリで手動発注:
   - 詳細パネル下部の「moomooで売る/買う準備」セクションを開く
   - 指値・数量をコピー
   - moomoo アプリに切り替え
   - 注文入力
5. 発注完了後、5 分以内に moomoo_sync で自動反映される
```

## 2.2 任意の手動投入

```
1. ダッシュボードの「手動投入」セクション
2. X発言や記事URL、テキストを貼り付け
3. 「分析する」ボタン
4. 5-15秒で結果表示
5. 必要なら「トピックスに追加」
```

## 2.3 価格更新

```
1. ヘッダー右上「↻ 価格を更新」を押す
2. または、サイドバーの小さい「↻ 更新」
3. 1-3秒で価格・含み損益が更新される
```

---

# 3. バックアップ戦略

## 3.1 何をバックアップするか

| 項目 | 重要度 | 復旧難易度 |
|---|---|---|
| SQLite DB | **最重要** | 不可逆 |
| .env | 高 | 再取得可能だが手間 |
| ログファイル | 低 | 再構築不可（過去分） |
| プロジェクトコード | 中 | git で復旧可能 |

## 3.2 自動バックアップ（日次）

```python
# scripts/backup.py
async def daily_backup():
    backup_dir = Path("~/.trading-agent/backups").expanduser()
    backup_dir.mkdir(exist_ok=True)

    today = date.today().isoformat()
    target = backup_dir / f"db_{today}.sqlite"

    # SQLite の VACUUM + BACKUP（オンラインバックアップ）
    src = Path("~/.trading-agent/db.sqlite").expanduser()
    shutil.copy2(src, target)

    # 古いバックアップを削除（30日分のみ保持）
    for old in sorted(backup_dir.glob("db_*.sqlite"))[:-30]:
        old.unlink()
```

APScheduler で JST 3:00（朝バッチ前）に実行。

## 3.3 手動バックアップ

```bash
# 任意のタイミングで実行
uv run python scripts/backup.py --label "before_param_change"
# → ~/.trading-agent/backups/db_2026-05-22_before_param_change.sqlite
```

## 3.4 復旧手順

```bash
# 1. プロセス停止
launchctl unload ~/Library/LaunchAgents/com.user.tradingagent.plist

# 2. DB ファイル置き換え
cp ~/.trading-agent/backups/db_2026-05-21.sqlite ~/.trading-agent/db.sqlite

# 3. プロセス再開
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist
```

## 3.5 Phase 2- のバックアップ拡張

- 外部ストレージ（NAS、S3 互換）への定期送信
- 差分バックアップ
- ポイントインタイム復旧

Phase 1 はローカル日次バックアップで十分。

---

# 4. アップグレード戦略

## 4.1 アップグレードの分類

| 種類 | 内容 | 頻度 |
|---|---|---|
| **パッチ** | バグ修正、軽微な調整 | 月1-2回 |
| **マイナー** | 機能追加、UIの変更 | 月1回 |
| **メジャー** | データモデル変更、Phase アップグレード | 数ヶ月に1回 |

## 4.2 パッチアップグレード手順

```bash
# 1. 停止
launchctl unload ~/Library/LaunchAgents/com.user.tradingagent.plist

# 2. バックアップ
uv run python scripts/backup.py --label "pre_upgrade"

# 3. コード更新
git pull

# 4. 依存パッケージ更新
uv sync

# 5. マイグレーション（あれば）
uv run alembic upgrade head

# 6. 再起動
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist

# 7. 動作確認
curl http://localhost:8000/health
```

## 4.3 マイナーアップグレード（データモデル変更）

Alembic のマイグレーションを使う：

```bash
# 1. バックアップ（必須）
uv run python scripts/backup.py --label "pre_minor_upgrade"

# 2. マイグレーション実行
uv run alembic upgrade head

# 3. 失敗時のロールバック
uv run alembic downgrade -1
```

Phase 1 中はマイグレーション運用は最小限。Phase 2 以降で正式化。

## 4.4 メジャーアップグレード

- 計画的に実施
- ペーパー口座で先行検証
- ダウンタイム想定

---

# 5. ログ管理

## 5.1 ログの種類

| ファイル | 内容 |
|---|---|
| `~/.trading-agent/logs/YYYY-MM-DD.log` | アプリケーションログ（structlog） |
| `~/.trading-agent/logs/stdout.log` | launchd の標準出力 |
| `~/.trading-agent/logs/stderr.log` | launchd の標準エラー |
| `~/.trading-agent/logs/morning_batch_YYYY-MM-DD.log` | 朝バッチ専用ログ |

## 5.2 ログローテーション

```python
# Python の logging.handlers.TimedRotatingFileHandler を使う
# 日次ローテーション、30日保持
```

ログサイズ：1日 10-50MB 想定。30日で 300MB-1.5GB 程度。

## 5.3 ログ検索

```bash
# 特定日の特定エージェントのログを抽出
cat ~/.trading-agent/logs/2026-05-22.log | jq 'select(.agent == "screening_agent")'

# エラーのみ抽出
cat ~/.trading-agent/logs/2026-05-22.log | jq 'select(.level == "ERROR")'
```

## 5.4 ログ可視化（Phase 2-）

- Grafana Loki 等で可視化
- Phase 1 は jq + grep で十分

---

# 6. モニタリング

## 6.1 監視すべき指標

| 指標 | しきい値 | アクション |
|---|---|---|
| 月次コスト | ¥4,000 超 | 警告、¥5,000 で停止 |
| 朝バッチ実行時間 | 30分超 | 警告、ログ確認 |
| エージェント失敗率 | 20% 超 | 警告、デバッグ |
| moomoo 切断時間 | 10分超 | クリティカル通知 |
| DB サイズ | 5GB 超 | アーカイブ実行 |
| メモリ使用量 | 4GB 超 | 警告、リーク調査 |

## 6.2 ダッシュボードでの可視化

サイドバー：
- Live インジケータ（健康状態）
- 月次コストバー

設定画面（Phase 1 後半で実装）：
- 朝バッチの履歴（成功/失敗）
- エージェント別の統計
- コスト推移

---

# 7. トラブルシューティング

## 7.1 起動しない

### 症状：`launchctl list` に表示されない

```bash
# plist の構文エラーを確認
plutil -lint ~/Library/LaunchAgents/com.user.tradingagent.plist

# launchd のログを確認
log show --predicate 'process == "launchd"' --last 1h
```

### 症状：起動するがすぐ落ちる

```bash
# stderr.log を確認
tail -50 ~/.trading-agent/logs/stderr.log

# よくある原因:
# 1. .env の読み込み失敗 → 必須キーが未設定
# 2. DB ファイルが見つからない → init_db.py を実行
# 3. ポート 8000 が使用中 → lsof -i :8000 で確認
```

## 7.2 moomoo に接続できない

```bash
# 1. moomoo OpenD が起動しているか確認
ps aux | grep moomoo

# 2. ポート 11111 が開いているか
nc -zv localhost 11111

# 3. moomoo OpenD のログを確認（GUI から見られる）

# 4. 接続パスワード（取引パスワード）が .env に正しく設定されているか
grep MOOMOO_TRADING_PWD .env
```

### 解決策
- moomoo OpenD を再起動
- ローカル接続を再有効化
- ファイアウォール確認

## 7.3 朝バッチが終わらない

```bash
# 状態確認
curl http://localhost:8000/api/batch_status

# ログ確認
tail -100 ~/.trading-agent/logs/morning_batch_$(date +%Y-%m-%d).log

# 緊急停止
curl -X POST http://localhost:8000/api/halt
```

### よくある原因
1. **LLM がレートリミット** → コスト超過、設定確認
2. **moomoo 切断** → degraded モードに切り替わる、自動継続
3. **NewsAPI 上限到達** → 自動的に RSS のみで継続
4. **特定エージェントのバグ** → ログから特定、該当エージェント停止

## 7.4 コストが想定より高い

```bash
# 直近の高コストな LLM 呼び出しを抽出
sqlite3 ~/.trading-agent/db.sqlite "
SELECT date, model, purpose, cost_jpy
FROM cost_logs
WHERE date >= date('now', '-7 days')
ORDER BY cost_jpy DESC
LIMIT 20
"
```

### 解決策
- 不要なエージェントを停止（設定）
- Cold Path への振替を増やす
- 重要度の低い処理を月次に変更

## 7.5 「価格が古い」警告が消えない

```bash
# moomoo 接続を確認
curl http://localhost:8000/health | jq '.components.moomoo_opend'

# 強制同期
curl -X POST http://localhost:8000/api/sync_moomoo

# キャッシュクリア（Phase 2- で実装、Phase 1 は再起動）
launchctl kickstart -k gui/$(id -u)/com.user.tradingagent
```

## 7.6 ダッシュボードがエラー画面

```bash
# 1. プロセスが動いているか
curl http://localhost:8000/health

# 2. ログを確認
tail -50 ~/.trading-agent/logs/$(date +%Y-%m-%d).log | grep ERROR

# 3. ブラウザのキャッシュをクリア（Ctrl+Shift+R）

# 4. それでもダメなら再起動
launchctl kickstart -k gui/$(id -u)/com.user.tradingagent
```

## 7.7 完全リセット（Phase 1 中は使うことがある）

```bash
# ⚠ 全データ消失
launchctl unload ~/Library/LaunchAgents/com.user.tradingagent.plist
rm -rf ~/.trading-agent/db.sqlite
rm -rf ~/.trading-agent/logs/*
uv run python scripts/init_db.py
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist
```

---

# 8. 緊急停止フロー

## 8.1 緊急停止のトリガー

以下の状況で停止：
- 想定外の自動売買が発生（Phase 2- 以降の懸念）
- LLM コストが急増
- moomoo に異常な発注が記録された
- システムが暴走している兆候

## 8.2 停止手順

### Level 1: HALT ファイル（推奨）

```bash
# ターミナルで一発
touch ~/.trading-agent/HALT
```

これだけで：
- 朝バッチが中止される
- 全エージェント実行が中止される
- ダッシュボードは表示されるが、新規分析は走らない

### Level 2: プロセス停止

```bash
launchctl unload ~/Library/LaunchAgents/com.user.tradingagent.plist
```

ダッシュボードへのアクセスも不可。完全停止。

### Level 3: moomoo OpenD 停止

```bash
# GUI から終了 or
killall MoomooOpenD
```

これで moomoo への発注 / 接続も完全停止。

## 8.3 再開手順

```bash
# Level 1 → HALT ファイル削除
rm ~/.trading-agent/HALT

# Level 2 → プロセス再開
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist

# Level 3 → moomoo OpenD を再起動
```

---

# 9. パフォーマンスチューニング

## 9.1 DB

```bash
# 月1回の VACUUM
sqlite3 ~/.trading-agent/db.sqlite "VACUUM;"

# 古い analysis_logs のアーカイブ（90日以上前）
sqlite3 ~/.trading-agent/db.sqlite "
DELETE FROM analysis_logs WHERE started_at < datetime('now', '-90 days');
"
```

## 9.2 ログ

```bash
# 30日以上前のログを圧縮
find ~/.trading-agent/logs -name "*.log" -mtime +30 -exec gzip {} \;

# 90日以上前は削除
find ~/.trading-agent/logs -name "*.gz" -mtime +90 -delete
```

## 9.3 メモリリーク対策

```bash
# 週次でプロセス再起動（メモリリーク対策）
# crontab に追加 or launchd の cron
0 4 * * 0 launchctl kickstart -k gui/$(id -u)/com.user.tradingagent
```

---

# 10. セキュリティ運用

## 10.1 API キーの保護

- `.env` は **絶対に git にコミットしない**（.gitignore で除外）
- 漏洩の懸念があれば即座にローテーション
- macOS のキーチェーンに保存する選択肢も（Phase 2-）

## 10.2 ログのサニタイズ

structlog の filter で：
- API キーを `***` に置換
- moomoo パスワードを `***` に置換

## 10.3 アクセス制限

- localhost バインドのみ（Phase 1）
- 外部アクセスは想定しない

## 10.4 取引履歴の保護

- 個人情報の集合 → 暗号化を Phase 2- で検討
- バックアップを外部ストレージに送る時は暗号化必須

---

# 11. 移行計画（Phase 1 → Phase 2）

Phase 1 で運用しながら Phase 2 への準備：

### 移行前にやること

| 項目 | 時期 |
|---|---|
| Phase 1 で 1-3 ヶ月運用 | 起点 |
| スコア精度の検証 | 月次 |
| パラメータの調整 | 月次 |
| 機能要望の蓄積 | 継続 |
| Phase 2 で実装する機能の優先順位付け | Phase 1 3ヶ月目 |

### 移行のチェックリスト（Phase 1 → Phase 2）

- [ ] ペーパー運用で 4 週間以上の稼働実績
- [ ] API 予算が見積もり内で安定
- [ ] 売り推奨の的中率が許容範囲（Phase 2 移行前の数値は別途決定）
- [ ] 緊急停止スイッチが機能している
- [ ] バックアップ・復旧手順が確認済み
- [ ] ユーザーが trace を通読してシステムに納得
- [ ] 発注の手動入力に「自動化したい」明確な体験的需要

### Phase 2 で追加する主要機能

| 機能 | 影響範囲 |
|---|---|
| broker_order MCP ツール | 発注自動化 |
| position-monitor エージェント | 保有銘柄の継続監視 |
| strategy-coordinator エージェント | 統合判断 |
| 長期戦略エンジン | サテライト枠の実体化 |
| ワンクリック発注UI | UI 拡張 |

詳細は Phase 1 運用後に再検討。

---

# 12. STEP C 完了 — STEP D への引き継ぎ

## 12.1 STEP C の成果物

| ファイル | 内容 |
|---|---|
| PANEL_SPECS.md | C-1：パネル別ロジック（7パネル）|
| SYSTEM_DESIGN.md | C-2：データモデル、MCP、moomoo、LLM、設定 |
| AGENT_SPECS.md | C-3：6エージェントの設計 |
| ORCHESTRATION.md | C-4：DAG、エラー処理、価格更新、同期 |
| OPERATIONS.md | C-5：このファイル |

## 12.2 STEP D で作成するもの

STEP D は **Claude Code への指示書**：

### 12.2.1 マスター指示書（CLAUDE_CODE_INSTRUCTIONS.md）

Claude Code が読む最初のドキュメント：

- プロジェクト概要（STEP_A_FINAL.md の要約）
- 設計書の読み方（どの順序で読むべきか）
- 実装の優先順位
- Phase 1 のゴール
- 守るべき原則

### 12.2.2 実装フェーズ計画

```
Phase 1.0：環境構築
  - プロジェクト構造（SYSTEM_DESIGN セクション 1.2）
  - 依存関係セットアップ
  - 設定ファイル
  - DB 初期化

Phase 1.1：MCP ツール基盤
  - base.py（基底クラス）
  - market_data, fundamentals, news, technicals, screening
  - 単体テスト

Phase 1.2：moomoo 連携
  - broker_read MCP ツール
  - BrokerConnection クラス
  - moomoo_sync ジョブ

Phase 1.3：エージェント基盤
  - base.py（基底クラス）
  - LLM ルーター
  - cost_logs / analysis_logs

Phase 1.4：エージェント実装
  - topics-collector
  - screening-agent
  - market-analyst
  - sell-recommender
  - portfolio-builder
  - manual-input-analyst

Phase 1.5：オーケストレーター
  - DAG エンジン
  - 朝バッチ実装
  - エラーハンドリング

Phase 1.6：UI（Next.js）
  - 各パネル実装
  - 詳細パネル
  - グラフ
  - インタラクション

Phase 1.7：常駐化と運用
  - launchd 設定
  - 健康チェック
  - バックアップ

Phase 1.8：4週間運用 + パラメータ調整
```

### 12.2.3 タスクの粒度

各 Phase のタスクを Claude Code が **1セッションで完結できる単位** に分解。

例：
- "MCP ツール market_data を実装"
- "screening-agent を実装、単体テスト含む"
- "保有銘柄カードの UI を実装"

各タスクには：
- 関連する設計書セクションへのリンク
- 期待される出力（コード、テスト）
- 完了の判定基準

---

# 13. 完了確認

STEP C-5 までで定義した：

- ✅ パネル別ロジック（C-1）
- ✅ システム設計（C-2）
- ✅ エージェント設計（C-3）
- ✅ オーケストレーション（C-4）
- ✅ 運用設計（C-5）

これで **Claude Code が実装を進められる状態** が整った。

STEP D（Claude Code への指示書）に進む準備が完了。

---

# 付録 A：日常運用のチートシート

頻繁に使うコマンドを集約：

```bash
# === 基本 ===
# プロセス状態
launchctl list | grep tradingagent

# 再起動
launchctl kickstart -k gui/$(id -u)/com.user.tradingagent

# ログ確認
tail -f ~/.trading-agent/logs/$(date +%Y-%m-%d).log

# === 緊急停止/再開 ===
touch ~/.trading-agent/HALT       # 停止
rm ~/.trading-agent/HALT          # 再開

# === 手動実行 ===
# 朝バッチ
curl -X POST http://localhost:8000/api/run_morning_batch

# 価格更新
curl -X POST http://localhost:8000/api/refresh_prices

# moomoo 同期
curl -X POST http://localhost:8000/api/sync_moomoo

# === DB 確認 ===
# 健康状態
curl http://localhost:8000/health | jq

# バッチ状態
curl http://localhost:8000/api/batch_status | jq

# 直近のレコメンド
sqlite3 ~/.trading-agent/db.sqlite \
  "SELECT date, ticker, action, score FROM decisions ORDER BY date DESC LIMIT 10"

# 月次コスト
sqlite3 ~/.trading-agent/db.sqlite \
  "SELECT date, SUM(cost_jpy) FROM cost_logs WHERE date >= date('now', '-30 days') GROUP BY date"

# === バックアップ ===
uv run python scripts/backup.py
ls -la ~/.trading-agent/backups/

# === 完全リセット（注意） ===
launchctl unload ~/Library/LaunchAgents/com.user.tradingagent.plist
mv ~/.trading-agent/db.sqlite ~/.trading-agent/db.sqlite.old
uv run python scripts/init_db.py
launchctl load ~/Library/LaunchAgents/com.user.tradingagent.plist
```

---

# 付録 B：FAQ

### Q: 朝バッチを実行する時刻を変更したい
A: settings テーブルの `morning_batch_time` を変更。UI からも変更可能（設定画面、Phase 1 後半）。

### Q: 特定の銘柄だけ買い候補から外したい
A: universe テーブルで `is_active = false` に設定。

### Q: 損切りラインを動的に調整したい
A: portfolio.stop_loss_pct を直接編集（SQL）。UI で編集できるのは Phase 2-。

### Q: 米国株だけにしたい
A: settings の screening 設定で market = "US" のみに絞る（Phase 1 では SQL で対応）。

### Q: AI のコストが心配
A: settings.monthly_budget_jpy で上限設定。デフォルト ¥5,000、変更可能。

### Q: 別のマシンに移行したい
A: ~/.trading-agent/ ディレクトリと プロジェクトディレクトリをコピー。.env も忘れずに。

### Q: 投資成績はどこで確認できる？
A: ダッシュボードの「予測の検証」セクション。詳細は週次レポート画面（Phase 1 後半）。

---

STEP C 完了。お疲れさまでした。
