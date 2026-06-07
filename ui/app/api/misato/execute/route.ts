/**
 * POST /api/misato/execute
 *
 * MISATO に予算を渡して、4 機への自動発注を**承認込み**で実行する。
 *   - body: { budget: number, personality?: string }
 *   - 安全装置: HALT ファイル / 1命令上限 / dry-run→approve の 2 段は misato.py 側に
 *
 * 実装: scripts/misato_dispatch.py --budget X [--personality P] --approve --json
 * 完了後に snapshot 再生成までやって UI に最新状態を返す。
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");

function runPython(
  scriptRel: string,
  args: string[] = [],
  extraEnv: Record<string, string> = {},
): Promise<{
  ok: boolean;
  stdout: string;
  stderr: string;
  code: number | null;
}> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", scriptRel, ...args], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, ...extraEnv },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => {
      stdout += b.toString();
    });
    child.stderr.on("data", (b) => {
      stderr += b.toString();
    });
    child.on("close", (code) => {
      resolve({ ok: code === 0, stdout, stderr, code });
    });
    child.on("error", (err) => {
      resolve({ ok: false, stdout, stderr: stderr + String(err), code: -1 });
    });
  });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const budget = Number(body?.budget ?? 0);
  const personality: string | undefined = body?.personality;
  // v2.8: 1 銘柄上限（金額直接 or treasury 比割合）
  const maxLotJpy = Number(body?.max_lot_jpy ?? NaN);
  const maxLotPct = Number(body?.max_lot_pct ?? NaN);

  if (!Number.isFinite(budget) || budget <= 0) {
    return NextResponse.json(
      { ok: false, error: "budget は正の数値が必要です" },
      { status: 400 },
    );
  }
  if (budget > 500_000) {
    return NextResponse.json(
      {
        ok: false,
        error: "1 命令あたり ¥500,000 が安全上限です（misato.py で物理クランプ）",
      },
      { status: 400 },
    );
  }

  // v2.8: 1 銘柄上限 → 環境変数で subprocess に渡す
  // 金額モード: WILLE_MAX_LOT_PCT を逆算で treasury 比に変換するか、別の env で渡す
  // 簡易: 金額モード時は budget を仮の "treasury" として扱い pct を逆算
  const extraEnv: Record<string, string> = {};
  if (Number.isFinite(maxLotPct) && maxLotPct > 0 && maxLotPct <= 1.0) {
    extraEnv.WILLE_MAX_LOT_PCT = String(maxLotPct);
  } else if (Number.isFinite(maxLotJpy) && maxLotJpy > 0) {
    // 金額モード: WILLE_MAX_LOT_JPY を直接渡す（lot_size 内で treasury 計算より優先）
    extraEnv.WILLE_MAX_LOT_JPY = String(maxLotJpy);
  }

  const args = ["--budget", String(budget), "--approve", "--json"];
  if (personality) {
    args.push("--personality", personality);
  }
  const dispatch = await runPython("scripts/misato_dispatch.py", args, extraEnv);
  const snap = await runPython("scripts/build_snapshot.py", [], extraEnv);

  let plan: unknown = null;
  try {
    plan = JSON.parse(dispatch.stdout);
  } catch {
    /* stdout が JSON でなければ無視 */
  }

  return NextResponse.json({
    ok: dispatch.ok && snap.ok,
    plan,
    dispatch_stderr: dispatch.stderr.slice(-2048),
    snapshot_ok: snap.ok,
    snapshot_stderr: snap.stderr.slice(-2048),
  });
}
