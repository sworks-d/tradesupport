# alembic/

マイグレーション環境（SYSTEM_DESIGN.md §2.5）。

Phase 1 の方針：**Alembic は導入のみ**。初期スキーマは `scripts/init_db.py` の
`create_all` で作成し、Phase 1 中の Breaking Change は DB を作り直して対応する
（個人利用・データ捨てて再構築可）。Phase 2 以降で正式にマイグレーション運用へ。

## 使い方（Phase 2 以降 / 任意）

```bash
# 現在のモデルからマイグレーションを自動生成
uv run alembic revision --autogenerate -m "説明"

# 適用
uv run alembic upgrade head
```

DB のパスは環境変数 `DB_PATH`（既定 `~/.trading-agent/db.sqlite`）を参照する。
