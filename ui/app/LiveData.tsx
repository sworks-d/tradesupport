"use client";

import { useEffect } from "react";

/**
 * B1の成果を UI に反映（第一歩）。
 * バックエンドが生成した /data/snapshot.json（実価格＋出典・時点・2ソース照合）を読み、
 * 保有カードへ「実価格」と「2ソース照合バッジ（照合済/不一致/未照合）」を反映する。
 * MAGI判定（3審判・GENDO・碇）は判断層 B2〜B5 構築後に反映。
 */
type Holding = {
  price_display: string | null;
  reconciliation: string; // ok / mismatch / single / cached
  as_of: string | null;
  source: string | null;
  pnl: { ratio_display: string; direction: string } | null; // 含み損益（取得単価×実価格）
  // X-2B holding_health（Kanchi T1-T5 翻案）
  health?: {
    state: "OK" | "WARN" | "REVIEW";
    triggers_fired: string[];
    evidence: { trigger_id: string; state: string; reason: string }[];
  };
};

const HEALTH_BADGE: Record<
  "OK" | "WARN" | "REVIEW",
  { text: string; cls: string; color: string }
> = {
  OK: { text: "規律OK", cls: "vb-ok", color: "#16a085" },
  WARN: { text: "規律WARN", cls: "vb-warn", color: "#e67e22" },
  REVIEW: { text: "規律REVIEW", cls: "vb-warn", color: "#c0392b" },
};
type JudgeMini = {
  judge: string;
  role: string;
  dot: string;
  verdict: string;
  verdict_word: string;
  color: string;
  dim: boolean;
  reason: string;
  counter?: string[]; // 自領域の反証（B群／信用性）
};
type Sizing = {
  amount_display: string;
  shares: number;
  weight_pct: number;
  price_jpy: number;
  note: string;
};
type Verification = {
  default_decision: string; // 保留 / 可
  flags: { label: string; status: string }[];
  unverified: string[];
};
type Commander = {
  recommendation: string;
  counter: string;
  src_note: string;
  compliant: boolean;
};
// GENDO推奨カード（初心者コーチ・守り主導・攻めは灰色）。build_snapshot の gendo_card に対応。
type GendoCardT = {
  action: string;
  sleeve: string;
  reason: string;
  counter: string;
  guardrail: string;
  defense_confidence: string;
  offense_confidence: string;
  learn_note: string;
};
type Candidate = {
  judges: JudgeMini[];
  split: string;
  split_interp?: string;
  gendo: string;
  sizing?: Sizing;
  verification?: Verification;
  commander?: Commander;
  gendo_card?: GendoCardT;
};
type Account = {
  cash: number;
  total_assets: number;
  cash_ratio: number;
  currency: string;
  positions: number;
};
type Snapshot = {
  generated_at: string;
  mode: string; // 価格ソース live/demo
  holdings_source: string; // 保有ソースの表示ラベル（サンプル/moomoo）
  usdjpy?: number;
  account?: Account;
  holdings: Record<string, Holding>;
  candidates?: Record<string, Candidate>; // {card_id: MAGI3審判}
};

// 詳細パネルの判定表示（.magi-jverdict）への可否マッピング
const JV: Record<string, { word: string; cls: string }> = {
  buy: { word: "買い", cls: "buy" },
  warn: { word: "慎重", cls: "warn" },
  hold: { word: "中立", cls: "warn" },
  sell: { word: "売り", cls: "no" },
  na: { word: "判定不能", cls: "no" },
};

const BADGE: Record<string, { text: string; cls: string }> = {
  ok: { text: "2ソース照合済", cls: "vb-ok" },
  mismatch: { text: "照合不一致", cls: "vb-warn" },
  single: { text: "未照合（単一ソース）", cls: "vb-warn" },
  cached: { text: "キャッシュ", cls: "vb-muted" },
};

export default function LiveData() {
  useEffect(() => {
    let cancelled = false;
    fetch("/data/snapshot.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((snap: Snapshot | null) => {
        if (!snap || cancelled) return;

        const holdingTickers = Object.keys(snap.holdings ?? {});
        if (holdingTickers.length === 0) {
          // 実稼働初期＝保有0（現金100%）。保有パネルは空状態にする
          const list = document.querySelector(".holdings-list");
          if (list && !list.querySelector(".holdings-empty")) {
            const total = (snap.account?.total_assets ?? 0).toLocaleString();
            list.innerHTML = `<div class="holdings-empty">保有なし（現金100% · ¥${total}）<div class="he-sub">買い候補から発注すると、ここに「予測 vs 実績」が表示されます</div></div>`;
          }
        } else {
          document.querySelectorAll<HTMLElement>(".hold").forEach((card) => {
          const ticker = card
            .querySelector(".hold-ticker")
            ?.textContent?.trim();
          if (!ticker) return;
          const h = snap.holdings[ticker];
          if (!h) return;

          const priceEl = card.querySelector<HTMLElement>(".hold-price");
          if (priceEl && h.price_display) priceEl.textContent = h.price_display;

          const pnlEl = card.querySelector<HTMLElement>(".hold-pnl");
          if (pnlEl && h.pnl) {
            pnlEl.textContent = h.pnl.ratio_display;
            pnlEl.classList.remove("up", "down");
            pnlEl.classList.add(h.pnl.direction);
          }

          const labels = card.querySelector(".hold-labels");
          if (labels && !labels.querySelector(".verify-badge")) {
            const meta = BADGE[h.reconciliation] ?? BADGE.single;
            const badge = document.createElement("span");
            badge.className = `hold-label verify-badge ${meta.cls}`;
            badge.textContent = meta.text;
            badge.title = `出典:${h.source ?? "-"} / 時点:${h.as_of ?? "-"} / 照合:${h.reconciliation}`;
            labels.appendChild(badge);
          }
          // X-2B holding_health バッジ（Kanchi T1-T5 翻案）
          if (labels && h.health && !labels.querySelector(".health-badge")) {
            const meta = HEALTH_BADGE[h.health.state] ?? HEALTH_BADGE.OK;
            const badge = document.createElement("span");
            badge.className = `hold-label health-badge ${meta.cls}`;
            badge.textContent = meta.text;
            badge.style.color = meta.color;
            badge.style.borderColor = meta.color;
            const triggerSummary = h.health.evidence
              .map((e) => `${e.trigger_id}:${e.reason}`)
              .join("\n");
            badge.title = `T1-T5: ${h.health.triggers_fired.join(", ") || "発火なし"}${triggerSummary ? "\n" + triggerSummary : ""}`;
            labels.appendChild(badge);
          }
          });
        }

        // === MAGI 買い候補に推奨サイジングを注入 ===
        // snapshot.candidates.<id>.sizing を読み、buy-zone の対応 .act に
        // "推奨 ¥X (Y株・PF比Z%・note)" 行を差し込む。
        const cands = snap.candidates ?? {};
        Object.entries(cands).forEach(([id, cand]) => {
          if (!cand?.sizing) return;
          const card = document.querySelector(
            `.buy-zone .act[data-detail="${id}"]`,
          );
          if (!card || card.querySelector(".magi-sizing")) return;
          const meta = card.querySelector(".forecast .forecast-meta");
          if (!meta) return;
          const sz = cand.sizing;
          const sizingDiv = document.createElement("div");
          sizingDiv.className = "magi-sizing";
          sizingDiv.innerHTML = `
            <span class="ms-label">推奨</span>
            <span class="ms-jpy">${sz.amount_display}</span>
            <span class="ms-detail">(${sz.shares}株・PF比 ${sz.weight_pct}%・${sz.note})</span>
          `;
          meta.parentNode?.insertBefore(sizingDiv, meta.nextSibling);
        });

        // === 保有 ↔ 売り注視の連動視覚化 ===
        // 売り Zone（推奨＋mini-act 注視）から ticker を集め、対応する保有カードに
        // MAGI 推奨バッジを足す。保有 = 守られているのか／売り推奨が来ているかを
        // 一目で見えるようにする。
        const sellMap = new Map<string, string>();
        document
          .querySelectorAll<HTMLElement>(".sell-zone .act")
          .forEach((act) => {
            const ticker = act
              .querySelector(".act-ticker")
              ?.textContent?.trim();
            const word =
              act.querySelector(".g-word")?.textContent?.trim() ?? "売り";
            if (ticker) sellMap.set(ticker, word);
          });
        document
          .querySelectorAll<HTMLElement>(".sell-zone .mini-act")
          .forEach((m) => {
            const ticker = m
              .querySelector(".mini-act-ticker")
              ?.textContent?.trim();
            if (ticker && !sellMap.has(ticker)) sellMap.set(ticker, "注視");
          });
        document.querySelectorAll<HTMLElement>(".hold").forEach((card) => {
          const ticker = card
            .querySelector(".hold-ticker")
            ?.textContent?.trim();
          if (!ticker || !sellMap.has(ticker)) return;
          const action = sellMap.get(ticker)!;
          const labels = card.querySelector(".hold-labels");
          if (labels && !labels.querySelector(".sell-watch-badge")) {
            const badge = document.createElement("span");
            badge.className = "hold-label sell-watch-badge";
            badge.textContent = `⚠ MAGI ${action}`;
            badge.title = `売り Zone と連動：MAGI が ${action} を推奨中。詳細パネルで根拠を確認`;
            labels.appendChild(badge);
          }
        });

        // 口座サマリ（現金・総資産）を反映
        if (snap.account) {
          const eq = document.querySelector(".sb-equity-value");
          if (eq) eq.textContent = `¥${snap.account.total_assets.toLocaleString()}`;
          const today = document.querySelector<HTMLElement>(".sb-equity-today");
          if (today) {
            today.textContent = "±¥0 (0.0%) 今日";
            today.classList.remove("up", "down");
          }
        }

        // 接続インジケータ（.sb-live）：保有ソースを正直に表示（サンプル/実口座の誤認防止）
        const live = document.querySelector(".sb-live");
        if (live) {
          const isReal = snap.holdings_source.startsWith("moomoo");
          live.classList.toggle("sb-live-sample", !isReal);
          live.innerHTML = `<span class="sb-live-dot"></span>${snap.holdings_source}`;
        }
        // 買い候補のMAGI 3審判を一覧ミニ（.magi-mini）へ反映
        Object.entries(snap.candidates ?? {}).forEach(([cardId, cand]) => {
          const card = document.querySelector(`.act[data-detail="${cardId}"]`);
          if (!card) return;
          // GENDO一語（判定から導出。割れなら「要検討」等）
          const gword = card.querySelector(".gendo-ind .g-word");
          if (gword) gword.textContent = cand.gendo;
          const mini = card.querySelector(".magi-mini");
          if (!mini) return;
          const mmds = mini.querySelectorAll(".mmd");
          cand.judges.forEach((j, i) => {
            const mmd = mmds[i];
            if (!mmd) return;
            const dot = mmd.querySelector(".dot");
            if (dot)
              dot.setAttribute(
                "style",
                `background:${j.dot}${j.dim ? ";opacity:0.45" : ""}`,
              );
            const vd = mmd.querySelector(".vd");
            if (vd) {
              vd.textContent = j.verdict_word;
              vd.setAttribute("style", `color:${j.color}`);
            }
          });
          const state = mini.querySelector(".magi-mini-state");
          if (state) {
            state.textContent = cand.split;
            if (cand.split_interp)
              (state as HTMLElement).title = cand.split_interp; // 割れ方の解釈
          }

          // 碇司令（B5）：推奨＋反対論拠を候補カードに表示
          if (cand.commander) {
            let cmdEl = card.querySelector<HTMLElement>(".cmd-line");
            if (!cmdEl && mini.parentElement) {
              cmdEl = document.createElement("div");
              cmdEl.className = "cmd-line";
              mini.parentElement.appendChild(cmdEl);
            }
            if (cmdEl) {
              cmdEl.textContent = `碇：${cand.commander.recommendation}`;
              cmdEl.title = `${cand.commander.counter}\n${cand.commander.src_note}`;
            }
          }

          // 予算内サイジング（¥1M・1銘柄20%上限・米株端株）を候補カードに表示
          if (cand.sizing) {
            let sizeEl = card.querySelector<HTMLElement>(".size-rec");
            if (!sizeEl && mini.parentElement) {
              sizeEl = document.createElement("div");
              sizeEl.className = "size-rec";
              mini.parentElement.insertBefore(sizeEl, mini.nextSibling);
            }
            if (sizeEl)
              sizeEl.textContent = `推奨：${cand.sizing.amount_display}・${cand.sizing.shares}株（資産${cand.sizing.weight_pct}%）`;
          }

          // 防御層（B3）：機械照合フラグ＋決裁前ゲート（既定保留）を表示
          if (cand.verification) {
            let vEl = card.querySelector<HTMLElement>(".verify-line");
            if (!vEl && mini.parentElement) {
              vEl = document.createElement("div");
              vEl.className = "verify-line";
              mini.parentElement.appendChild(vEl);
            }
            if (vEl) {
              const flags = cand.verification.flags
                .map((f) => `${f.label} ${f.status === "ok" ? "✓" : "⚠"}`)
                .join(" ｜ ");
              const hold = cand.verification.default_decision === "保留";
              vEl.textContent = `決裁既定：${cand.verification.default_decision} ｜ ${flags}`;
              vEl.classList.toggle("vl-hold", hold);
            }
          }

          // 詳細パネル（決裁画面）にも反映。役割名で行を対応づけ、要素が無ければスキップ
          const panel = document.querySelector(`[data-panel="${cardId}"]`);
          if (panel) {
            const byRole: Record<string, JudgeMini> = {};
            cand.judges.forEach((j) => (byRole[j.role] = j));
            panel.querySelectorAll(".magi-jrow").forEach((row) => {
              const name = row.querySelector(".magi-jname")?.textContent?.trim();
              const j = name ? byRole[name] : undefined;
              if (!j) return;
              const m = JV[j.verdict] ?? JV.na;
              const vd = row.querySelector(".magi-jverdict");
              if (vd) {
                vd.textContent = m.word;
                vd.className = `magi-jverdict ${m.cls}`;
              }
              const reason = row.querySelector(".magi-jreason");
              if (reason) {
                // 反証（自領域の逆向き事実）があれば根拠文に併記（要素は増やさない）
                const counter = j.counter && j.counter.length
                  ? `　／反証：${j.counter.join("・")}`
                  : "";
                reason.textContent = `${j.reason}${counter}`;
              }
            });
            const mfState = panel.querySelector(".mf-state");
            if (mfState) mfState.textContent = cand.split;
            if (cand.commander) {
              const rec = panel.querySelector(".cmd-rec");
              if (rec) rec.textContent = cand.commander.recommendation;
              const cnt = panel.querySelector(".cmd-counter");
              if (cnt)
                cnt.innerHTML = `<b>反対するなら：</b>${cand.commander.counter.replace(/^反対するなら：/, "")}`;
              const src = panel.querySelector(".cmd-src");
              if (src) src.textContent = cand.commander.src_note;
            }
            // GENDO推奨カード：碇ゾーンを初心者向けの「推奨アクション＋ガードレール＋確信度」に格上げ
            if (cand.gendo_card) {
              const gc = cand.gendo_card;
              const sleeve = gc.sleeve !== "—" ? `（${gc.sleeve}）` : "";
              const rec = panel.querySelector(".cmd-rec");
              if (rec) rec.textContent = `GENDO推奨：${gc.action}${sleeve}　${gc.reason}`;
              const src = panel.querySelector(".cmd-src");
              if (src)
                src.textContent =
                  `従うなら：${gc.guardrail}　｜　確信度 守り${gc.defense_confidence}` +
                  `／攻め${gc.offense_confidence}　｜　${gc.learn_note}`;
            }
            if (cand.verification) {
              const flagEls = panel.querySelectorAll(".magi-flag");
              cand.verification.flags.forEach((f, i) => {
                const el = flagEls[i];
                if (el)
                  el.textContent = `${f.label} ${f.status === "ok" ? "✓" : "⚠"}`;
              });
            }
          }
        });

        // === モック銘柄／統計の全面クリーン化（ローカル UI は実データだけを見せる）===
        // dashboard.html は F0 ゴールデンマスター（デザインリファレンス）として
        // サンプル銘柄が焼き込まれている。実運用 UI では snapshot に対応データが
        // 無いカード／統計を非表示・ゼロ化して、ユーザーがモックを実データと誤認しないようにする。
        const isHoldingsEmpty = holdingTickers.length === 0;
        const sellSection =
          ((snap as unknown) as { sell?: Record<string, unknown> }).sell ?? {};
        const sellIds = new Set(Object.keys(sellSection));
        const allocation =
          ((snap as unknown) as {
            allocation?: {
              current: { core: number; satellite: number; cash: number };
            };
          }).allocation;
        const totalAssets = snap.account?.total_assets ?? 0;

        // --- サイドバー .alloc-list を allocation.current で上書き ---
        if (allocation) {
          const allocList = document.querySelector(".alloc-list");
          if (allocList) {
            const tiers: { label: string; color: string; value: number }[] = [
              { label: "Core（ETF・高配当）", color: "#16a085", value: allocation.current.core },
              { label: "Satellite（個別株）", color: "#ff8c42", value: allocation.current.satellite },
              { label: "現金（防御余力）", color: "#888888", value: allocation.current.cash },
            ];
            allocList.innerHTML = tiers
              .map((t) => {
                const pct = totalAssets > 0 ? Math.round((t.value / totalAssets) * 100) : 0;
                const k = (t.value / 1000).toFixed(1);
                return `
                  <div class="alloc-row">
                    <div class="alloc-head">
                      <span class="alloc-name">
                        <span class="alloc-dot" style="background:${t.color}"></span>
                        ${t.label}
                      </span>
                      <span>
                        <span class="alloc-amount">¥${k}k</span>
                        <span class="alloc-pct">${pct}%</span>
                      </span>
                    </div>
                    <div class="alloc-bar"><div class="alloc-bar-fill" style="width:${pct}%; background:${t.color}"></div></div>
                  </div>
                `;
              })
              .join("");
          }
          // 配分セクションの見出し ¥108k → 実 total
          document.querySelectorAll<HTMLElement>(".sb-section-title .sub").forEach((el) => {
            if (el.textContent?.includes("¥")) {
              el.textContent = `¥${Math.round(totalAssets / 1000)}k`;
            }
          });
        }

        // --- サイドバー「今日のサマリー」を実データに（売り/買い/警告/的中率） ---
        const todayItems = document.querySelectorAll<HTMLElement>(".sb-today-list .sb-today-item");
        const candIdsForSummary = Object.keys(snap.candidates ?? {});
        const summaryValues: { sellCount: number; buyCount: number; warnCount: number; hitRate: string } = {
          sellCount: isHoldingsEmpty ? 0 : Object.keys(sellSection).length,
          buyCount: candIdsForSummary.length,
          warnCount: 0,
          hitRate: "—",
        };
        const summaryOrder: ("sellCount" | "buyCount" | "warnCount" | "hitRate")[] = [
          "sellCount",
          "buyCount",
          "warnCount",
          "hitRate",
        ];
        todayItems.forEach((item, i) => {
          const v = item.querySelector<HTMLElement>(".sb-today-value");
          if (!v) return;
          const key = summaryOrder[i];
          if (key === "hitRate") {
            v.textContent = String(summaryValues.hitRate);
          } else {
            v.textContent = `${summaryValues[key]} 件`;
          }
        });

        // --- サイドバー「月次コスト」を 0 にリセット（cost_logs 未配線のため暫定） ---
        const costVal = document.querySelector<HTMLElement>(".sb-cost-head .val");
        if (costVal) costVal.textContent = "¥0 / ¥5,000";
        const costFill = document.querySelector<HTMLElement>(".sb-cost-fill");
        if (costFill) costFill.style.width = "0%";
        const costSub = document.querySelector<HTMLElement>(".sb-cost-sub");
        if (costSub) costSub.textContent = "予算の 0%";

        // --- サイドバー：スパークライン / 累計30日 / 本日変動を「データなし」化 ---
        const spark = document.querySelector<HTMLElement>(".sb-spark");
        if (spark && !spark.querySelector(".sb-spark-empty")) {
          spark.innerHTML = `<div class="sb-spark-empty" style="height:48px;display:flex;align-items:center;justify-content:center;color:var(--ts-muted,#888);font-size:12px;">価格履歴データなし</div>`;
        }
        const cumul = document.querySelector<HTMLElement>(".sb-equity-cumulative .val");
        if (cumul) {
          cumul.textContent = "—";
          cumul.classList.remove("up", "down");
        }

        // --- 保有 Zone：ダミーカードを明示的に非表示（innerHTML 置換と二重で確実に） ---
        if (isHoldingsEmpty) {
          document.querySelectorAll<HTMLElement>(".holdings-list .hold").forEach((card) => {
            card.style.display = "none";
          });
        }

        // --- 警告パネル / 決算予定パネルを「データなし」化 ---
        const miniCards = document.querySelectorAll<HTMLElement>(".secondary-row .mini-card");
        miniCards.forEach((card) => {
          const titleText = card.querySelector(".mini-card-title")?.textContent ?? "";
          const count = card.querySelector<HTMLElement>(".mini-card-count");
          const isAlerts = titleText.includes("警告");
          const isEarnings = titleText.includes("決算");
          if (count) count.textContent = isEarnings ? "今週 0件" : "0件";
          // 中身（.alert や .info-list）を「データなし」プレースホルダに差し替える
          card
            .querySelectorAll<HTMLElement>(".alert, .info-list")
            .forEach((el, idx) => {
              if (idx > 0) {
                el.remove();
                return;
              }
              el.outerHTML = `<div class="mini-card-empty" style="padding:18px;color:var(--ts-muted,#888);font-size:13px;">${
                isAlerts ? "今日の警告はなし" : isEarnings ? "今週の決算予定はなし（カレンダー未配線）" : "データなし"
              }</div>`;
            });
        });

        // --- 予測検証（Track Record）を「データなし」化 ---
        document
          .querySelectorAll<HTMLElement>(".track-stats .track-stat")
          .forEach((stat) => {
            const v = stat.querySelector<HTMLElement>(".track-stat-value");
            const s = stat.querySelector<HTMLElement>(".track-stat-sub");
            if (v) {
              v.textContent = "—";
              v.classList.remove("up", "down");
            }
            if (s) s.textContent = "履歴なし";
          });

        // --- Topics：snapshot.topics があれば動的生成、無ければデータなし表示 ---
        const escapeHtml = (s: string): string =>
          s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
        const impLabel = (imp: string): string =>
          imp === "high" ? "重要" : imp === "medium" ? "中" : "低";
        type TopicItem = {
          id: number;
          importance: string;
          category: string;
          headline: string;
          summary: string;
          source: string;
          url: string;
          affected_tickers: string[];
          impact_text: string;
          collected_at: string;
        };
        const topics = ((snap as unknown) as {
          topics?: { counts: Record<string, number>; items: TopicItem[] };
        }).topics;

        if (topics && topics.items && topics.items.length > 0) {
          // タブの count を category 別に
          document.querySelectorAll<HTMLElement>(".topics-tabs .topics-tab").forEach((tab) => {
            const key = tab.getAttribute("data-topic-tab") ?? "all";
            const c = tab.querySelector<HTMLElement>(".count");
            if (c) c.textContent = String(topics.counts?.[key] ?? 0);
          });
          // リスト再生成（data-topic-content に応じて絞り込み）
          document.querySelectorAll<HTMLElement>(".topics-list").forEach((list) => {
            const filter = list.getAttribute("data-topic-content") ?? "all";
            const filtered =
              filter === "all"
                ? topics.items
                : topics.items.filter((t) => t.category === filter);
            if (filtered.length === 0) {
              list.innerHTML = `<div class="topics-empty" style="padding:24px;color:var(--ts-muted,#888);font-size:13px;">このカテゴリには Topics がありません。</div>`;
              return;
            }
            list.innerHTML = filtered
              .map((t) => {
                const tickers = t.affected_tickers.length
                  ? `<span class="topic-tickers" style="margin-left:8px;font-size:11px;opacity:0.7;">影響: ${t.affected_tickers.map(escapeHtml).join(" / ")}</span>`
                  : "";
                const urlLink = t.url
                  ? `<a href="${escapeHtml(t.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;">${escapeHtml(t.source)}</a>`
                  : escapeHtml(t.source);
                return `
                  <div class="topic imp-${escapeHtml(t.importance)}" data-cat="${escapeHtml(t.category)}">
                    <div class="topic-imp">
                      <span class="topic-imp-badge">${impLabel(t.importance)}</span>
                      <span class="topic-time">${escapeHtml(t.collected_at)}</span>
                    </div>
                    <div class="topic-body">
                      <div class="topic-headline">${escapeHtml(t.headline)}</div>
                      <div class="topic-summary">${escapeHtml(t.summary)}</div>
                      <div class="topic-meta" style="margin-top:6px;font-size:12px;opacity:0.7;">
                        <span class="topic-source">${urlLink}</span>
                        ${tickers}
                      </div>
                    </div>
                  </div>
                `;
              })
              .join("");
          });
        } else {
          // フォールバック：データなし
          document.querySelectorAll<HTMLElement>(".topics-tabs .count").forEach((c) => {
            c.textContent = "0";
          });
          document.querySelectorAll<HTMLElement>(".topics-list").forEach((list) => {
            if (!list.querySelector(".topics-empty")) {
              list.innerHTML = `<div class="topics-empty" style="padding:24px;color:var(--ts-muted,#888);font-size:13px;">Topics は朝バッチで収集されます（現在データなし）。</div>`;
            }
          });
        }

        // --- 決裁待ち decisions を buy-zone に動的描画（dashboard.html .act.buy 構造に準拠） ---
        type PendingDecision = {
          decision_id: number;
          ticker: string;
          name?: string;
          market?: string;
          sector?: string;
          action: string;
          status: string;
          gendo_stance: string;
          thesis: string;
          entry_price: number | null;
          stop_pct: number | null;
          target_period_days: number | null;
          score: number | null;
          expected_return: number | null;
          commander_recommendation: string | null;
          commander_counter: string | null;
          verdicts: Record<string, string>;
          price_history_30d?: number[];
        };
        const pendingDecisions = ((snap as unknown) as {
          pending_decisions?: Record<string, PendingDecision>;
        }).pending_decisions ?? {};
        const pendingList = Object.values(pendingDecisions);

        // MAGI 3 審判の固定メタ（dashboard.html と完全に同じ色・役割名）
        const JUDGE_META: Record<string, { dot: string; role: string }> = {
          MELCHIOR: { dot: "#ff8c42", role: "業績" },
          BALTHASAR: { dot: "#4ecdc4", role: "株価" },
          CASPER: { dot: "#fbbf24", role: "文脈" },
        };
        const VD: Record<string, { word: string; color: string; dim: boolean }> = {
          buy: { word: "買", color: "var(--up)", dim: false },
          warn: { word: "慎重", color: "var(--warn)", dim: false },
          hold: { word: "中立", color: "var(--ink-2)", dim: false },
          sell: { word: "売", color: "var(--down)", dim: false },
          na: { word: "不能", color: "var(--ink-3)", dim: true },
        };

        const buyZoneItems = document.querySelector<HTMLElement>(".buy-zone .zone-items");
        if (buyZoneItems && pendingList.length > 0) {
          buyZoneItems.innerHTML = pendingList
            .map((d) => {
              const stance = d.gendo_stance || "—";
              const market = d.market || "JP";
              const sector = d.sector || "";
              const name = d.name || "";
              const summary = d.commander_recommendation
                ? d.commander_recommendation
                : d.thesis || "MAGI 評価待ち";

              // MAGI dots（dashboard.html と同一マークアップ）
              const judges = ["MELCHIOR", "BALTHASAR", "CASPER"]
                .map((j) => {
                  const meta = JUDGE_META[j];
                  const verdict = d.verdicts?.[j] ?? "na";
                  const vd = VD[verdict] ?? VD.na;
                  const dotStyle = vd.dim
                    ? `background:${meta.dot};opacity:0.45`
                    : `background:${meta.dot}`;
                  return `<span class="mmd"><span class="dot" style="${dotStyle}"></span><span class="jn">${j}</span><span class="jr">${meta.role}</span><span class="vd" style="color:${vd.color}">${escapeHtml(vd.word)}</span></span>`;
                })
                .join("");

              // magi-mini-state：voting=MELCHIOR+CASPER の合意度
              const vMel = d.verdicts?.MELCHIOR ?? "na";
              const vCas = d.verdicts?.CASPER ?? "na";
              const buyVotes = [vMel, vCas].filter((v) => v === "buy").length;
              const naCount = [vMel, vCas].filter((v) => v === "na").length;
              const stateText =
                naCount > 0
                  ? `${naCount}/2 判定不能`
                  : buyVotes === 2
                    ? "全員一致・買い"
                    : buyVotes === 1
                      ? "1/2 買い・割れ"
                      : "買いゼロ";

              const sqlCmd = `UPDATE decisions SET status='approved' WHERE id=${d.decision_id};`;

              // 価格推移 sparkline（過去 30 日の終値から生成）
              const ph = d.price_history_30d ?? [];
              let forecastBlock = "";
              if (ph.length >= 2) {
                const first = ph[0];
                const last = ph[ph.length - 1];
                const minV = Math.min(...ph);
                const maxV = Math.max(...ph);
                const range = maxV - minV || 1;
                const W = 320;
                const H = 60;
                const pts = ph
                  .map((v, i) => {
                    const x = (i / (ph.length - 1)) * (W - 4) + 2;
                    const y = H - ((v - minV) / range) * (H - 8) - 4;
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ");
                const pct = first > 0 ? ((last - first) / first) * 100 : 0;
                const isUp = pct >= 0;
                const lineColor = isUp ? "#4ade80" : "#f87171";
                const fillColor = isUp ? "#4ade8022" : "#f8717122";
                const sign = isUp ? "+" : "";
                const lastX = (W - 4) + 2;
                const lastY = H - ((last - minV) / range) * (H - 8) - 4;
                const areaPts = `2,${H} ${pts} ${lastX.toFixed(1)},${H}`;
                const isJp = (d.market || "JP") === "JP";
                const priceStr = isJp
                  ? `¥${Math.round(last).toLocaleString()}`
                  : `$${last.toFixed(2)}`;
                forecastBlock = `
                  <div class="forecast" style="margin-top:10px;">
                    <div class="forecast-meta">
                      <span class="lbl">30日前</span>
                      <span class="val" style="color:${lineColor};font-weight:600;">${sign}${pct.toFixed(1)}% / 現在 ${priceStr}</span>
                      <span class="lbl">${ph.length}日分</span>
                    </div>
                    <svg class="forecast-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:60px;">
                      <line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#6b6962" stroke-width="0.4" stroke-dasharray="2,2" opacity="0.5"/>
                      <polygon points="${areaPts}" fill="${fillColor}"/>
                      <polyline fill="none" stroke="${lineColor}" stroke-width="1.6" points="${pts}"/>
                      <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="3" fill="${lineColor}"/>
                    </svg>
                  </div>
                `;
              } else {
                forecastBlock = `
                  <div class="forecast" style="margin-top:10px;">
                    <div class="forecast-meta">
                      <span class="lbl">価格履歴データなし（yfinance 取得失敗）</span>
                    </div>
                  </div>
                `;
              }

              return `
                <div class="act buy" data-detail="d${d.decision_id}">
                  <div class="act-top">
                    <div class="gendo-ind">
                      <div class="g-face" aria-hidden="true">碇</div>
                      <div class="g-word">${escapeHtml(stance)}</div>
                      <div class="g-lbl">GENDO</div>
                    </div>
                    <div>
                      <div class="act-head">
                        <span class="act-ticker">${escapeHtml(d.ticker)}</span>
                        <span class="act-market">${escapeHtml(market)}${sector ? " " + escapeHtml(sector) : ""}</span>
                        ${name ? `<span class="act-name">${escapeHtml(name)}</span>` : ""}
                      </div>
                      <div class="act-summary">${escapeHtml(summary)}</div>
                      <div class="magi-mini">
                        <div class="magi-mini-dots">${judges}</div>
                        <span class="magi-mini-state">${escapeHtml(stateText)}</span>
                      </div>
                      <div class="cmd-line" style="margin-top:6px;font-size:11px;opacity:0.85;">承認: <code style="font-family:'JetBrains Mono',ui-monospace,monospace;font-size:10px;user-select:all;">${escapeHtml(sqlCmd)}</code></div>
                    </div>
                    <div class="act-arrow">→</div>
                  </div>
                  ${forecastBlock}
                </div>
              `;
            })
            .join("");
        }

        // --- 詳細パネル：snapshot.candidates に対応 ID が無いものは非表示 ---
        const visiblePanelIds = new Set(candIdsForSummary);
        document
          .querySelectorAll<HTMLElement>(".detail-panel[data-panel]")
          .forEach((p) => {
            const id = p.getAttribute("data-panel") ?? "";
            if (!visiblePanelIds.has(id)) {
              p.style.display = "none";
            }
          });

        // --- buy/sell zone のヘッダ count を実データに（pending_decisions も含む）---
        const buyCount = candIdsForSummary.length + pendingList.length;
        const sellCount = summaryValues.sellCount;
        const buyZoneCount = document.querySelector<HTMLElement>(".buy-zone .zone-count");
        if (buyZoneCount) {
          buyZoneCount.innerHTML =
            buyCount === 0 ? "0件" : `<strong>${buyCount}</strong>件 決裁待ち`;
        }
        const sellZoneCount = document.querySelector<HTMLElement>(".sell-zone .zone-count");
        if (sellZoneCount) {
          sellZoneCount.innerHTML =
            sellCount === 0 ? "0件" : `<strong>${sellCount}</strong>件 推奨`;
        }

        // --- サイドバー「今日のサマリー」の買い推奨を pending 含む値に再上書き ---
        const buyTodayValue = todayItems[1]?.querySelector<HTMLElement>(".sb-today-value");
        if (buyTodayValue) buyTodayValue.textContent = `${buyCount} 件`;

        // --- ナビバッジ（ダッシュボードの "4"）も 0 件に ---
        document.querySelectorAll<HTMLElement>(".sb-nav .nav-badge").forEach((b) => {
          // 数値らしきテキストだけ 0 化（ZEELE の「攻め」など文字バッジは触らない）
          if (/^\d+$/.test(b.textContent?.trim() ?? "")) {
            b.textContent = "0";
          }
        });

        // 売り Zone：snapshot.sell に無い ID は非表示（保有 0 なら全部）。
        document
          .querySelectorAll<HTMLElement>(".sell-zone .act")
          .forEach((act) => {
            const id = act.getAttribute("data-detail") ?? "";
            if (isHoldingsEmpty || !sellIds.has(id)) {
              act.style.display = "none";
            }
          });
        document
          .querySelectorAll<HTMLElement>(".sell-zone .mini-act")
          .forEach((m) => {
            const id = m.getAttribute("data-detail") ?? "";
            if (isHoldingsEmpty || !sellIds.has(id)) {
              m.style.display = "none";
            }
          });

        // 売り Zone 自体が空になったら、プレースホルダー文言に差し替える。
        const sellZone = document.querySelector<HTMLElement>(".sell-zone");
        if (
          sellZone &&
          sellZone.querySelectorAll<HTMLElement>(".act:not([style*='display: none'])").length === 0 &&
          !sellZone.querySelector(".sell-empty")
        ) {
          const sub = sellZone.querySelector(".zone-subtitle");
          const empty = document.createElement("div");
          empty.className = "sell-empty";
          empty.style.cssText =
            "padding:24px;color:var(--ts-muted,#888);font-size:13px;line-height:1.6;";
          empty.textContent = isHoldingsEmpty
            ? "保有なし — 売り推奨はありません（買い → 紙約定で保有が入ると、ここに MAGI 売り判定が並びます）"
            : "今日の MAGI 売り推奨はなし（保有は規律 OK 状態）";
          (sub?.parentElement ?? sellZone).appendChild(empty);
        }

        // 買い Zone：snapshot.candidates に無い data-detail は非表示。
        // ただし `d<N>` プレフィックスは決裁待ち動的カード（後段で innerHTML 置換で挿入）
        // なので非表示対象から除外する。
        const candIds = new Set(Object.keys(snap.candidates ?? {}));
        const isPendingCard = (id: string) => /^d\d+$/.test(id);
        document
          .querySelectorAll<HTMLElement>(".buy-zone .act")
          .forEach((act) => {
            const id = act.getAttribute("data-detail") ?? "";
            if (!candIds.has(id) && !isPendingCard(id)) {
              act.style.display = "none";
            }
          });
        document
          .querySelectorAll<HTMLElement>(".buy-zone .mini-act")
          .forEach((m) => {
            const id = m.getAttribute("data-detail") ?? "";
            if (!candIds.has(id) && !isPendingCard(id)) {
              m.style.display = "none";
            }
          });

        // 取得時刻＝価格の話に限定（保有ソースとは分ける）
        const sb = document.querySelector(".sb-last-updated span:first-child");
        if (sb) sb.textContent = `${snap.generated_at} 取得・価格 ${snap.mode}`;
      })
      .catch(() => {
        /* スナップショット未生成時は無反映（モックのまま） */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
