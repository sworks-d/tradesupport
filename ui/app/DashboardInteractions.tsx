"use client";

import { useEffect } from "react";

/**
 * ダッシュボードの残りインタラクションを配線（注入DOMにイベント委譲）。
 * - リフレッシュ（価格フラッシュの擬似更新）と相対時刻
 * - トピックスのタブ絞込
 * - 「他の候補/他の注視」の展開（more-toggle）
 * - チャートのホバーツールチップ（hover-point / track-point）
 * F4の先取り。データ結線(F3)で擬似更新は実APIに、F2で React state へ移す。
 */
export default function DashboardInteractions() {
  useEffect(() => {
    let lastUpdateTime = Date.now();

    const formatRelative = (ms: number) => {
      const sec = Math.floor(ms / 1000);
      if (sec < 60) return `${sec}秒前`;
      const min = Math.floor(sec / 60);
      if (min < 60) return `${min}分前`;
      return `${Math.floor(min / 60)}時間前`;
    };

    const updateRelativeTime = () => {
      const elapsed = Date.now() - lastUpdateTime;
      const rel = elapsed < 5000 ? "たった今" : formatRelative(elapsed);
      const el = document.getElementById("updated-time");
      if (el) el.textContent = rel;
      const sbTime = document.querySelector(
        ".sb-last-updated span:first-child",
      );
      if (sbTime) sbTime.textContent = `${rel} 取得`;
    };
    const interval = window.setInterval(updateRelativeTime, 10000);

    const simulatePriceUpdate = () => {
      lastUpdateTime = Date.now();
      updateRelativeTime();
      document.querySelectorAll(".hold-price, .hold-pnl").forEach((cell) => {
        const isUp = Math.random() > 0.5;
        cell.classList.remove("flash-up", "flash-down");
        void (cell as HTMLElement).offsetWidth; // reflow
        cell.classList.add(isUp ? "flash-up" : "flash-down");
      });
      const eq = document.querySelector(".sb-equity-value");
      if (eq) {
        eq.classList.remove("flash-up", "flash-down");
        void (eq as HTMLElement).offsetWidth;
        eq.classList.add("flash-up");
      }
    };

    const doRefresh = (btn: Element) => {
      if (btn.classList.contains("spinning")) return;
      btn.classList.add("spinning");

      // v2.8: localStorage から 1 銘柄上限を読んで API に渡す
      const mode = window.localStorage.getItem("ds_max_lot_mode") ?? "jpy";
      const body: Record<string, number> = {};
      if (mode === "pct") {
        const pct = Number(window.localStorage.getItem("ds_max_lot_pct") ?? "100");
        if (Number.isFinite(pct) && pct > 0) body.max_lot_pct = pct / 100;
      } else {
        const jpy = Number(window.localStorage.getItem("ds_max_lot_jpy") ?? "100000");
        if (Number.isFinite(jpy) && jpy > 0) body.max_lot_jpy = jpy;
      }

      // v2.10: /api/refresh は重い（MISATO dispatch + snapshot 再生成、十数分）+
      // 連打で多重起動・ファイルロック競合する問題があるため、
      // 軽量な /api/refresh-prices（yfinance 1 リクエスト・1-2 秒・無料）に切替。
      // 重い再生成は朝バッチ（1 日 1 回 launchd）に任せる方針。
      fetch("/api/refresh-prices", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      })
        .then((r) => r.json().catch(() => null))
        .then((res) => {
          if (res?.ok) {
            // snapshot.json が更新されたのでハードリロード（キャッシュバイパス）
            window.location.reload();
          } else {
            simulatePriceUpdate();
            btn.classList.remove("spinning");
            // eslint-disable-next-line no-console
            console.warn("[refresh] failed:", res);
          }
        })
        .catch((err) => {
          // ネットワーク / 起動失敗時は擬似更新フォールバック
          simulatePriceUpdate();
          btn.classList.remove("spinning");
          // eslint-disable-next-line no-console
          console.warn("[refresh] error:", err);
        });
    };

    // ホバーツールチップ用の要素
    const tooltip = document.createElement("div");
    tooltip.className = "tooltip";
    document.body.appendChild(tooltip);

    const positionTooltip = (e: MouseEvent) => {
      const tw = tooltip.offsetWidth;
      const th = tooltip.offsetHeight;
      let x = e.clientX + 12;
      let y = e.clientY - th - 8;
      if (x + tw > window.innerWidth) x = e.clientX - tw - 12;
      if (y < 0) y = e.clientY + 16;
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    };

    // v2.8: broker_mode を初期表示時に body class へ反映 + サイドバートグル動作
    const applyBrokerMode = (mode: "paper" | "live") => {
      document.body.classList.toggle("live-mode-active", mode === "live");
      document.body.classList.toggle("paper-mode-active", mode === "paper");
      const paperBtn = document.getElementById("sb-mode-paper");
      const liveBtn = document.getElementById("sb-mode-live");
      paperBtn?.classList.toggle("is-on", mode === "paper");
      liveBtn?.classList.toggle("is-on", mode === "live");
      const note = document.getElementById("sb-mode-note");
      if (note) {
        note.textContent =
          mode === "live"
            ? "⚡ moomoo OpenD 経由でリアルマネー発注"
            : "DB 記録のみ・リアルマネー触らない";
      }
    };
    // 初期化：localStorage → API GET の順で取得
    const storedMode = window.localStorage.getItem("wille_broker_mode");
    if (storedMode === "paper" || storedMode === "live") {
      applyBrokerMode(storedMode);
    }
    fetch("/api/wille/broker-mode")
      .then((r) => r.json())
      .then((j) => {
        if (j?.mode === "paper" || j?.mode === "live") {
          window.localStorage.setItem("wille_broker_mode", j.mode);
          applyBrokerMode(j.mode);
        }
      })
      .catch(() => {});

    const handleBrokerModeToggle = async (mode: "paper" | "live") => {
      if (mode === "live") {
        const ok = window.confirm(
          "⚡ 本番モードに切替えます。\n\n" +
            "・moomoo OpenD 経由でリアルマネー発注になります\n" +
            "・「承認・実行」ボタンで実際の取引が発生\n" +
            "・サイドバー総資産は moomoo 実口座残高を参照\n\n" +
            "本当に切替えますか？",
        );
        if (!ok) return;
      }
      try {
        const res = await fetch("/api/wille/broker-mode", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ mode }),
        });
        const j = await res.json();
        if (j?.ok) {
          window.localStorage.setItem("wille_broker_mode", mode);
          applyBrokerMode(mode);
          // snapshot 再生成済 → 1 秒後にリロード
          setTimeout(() => window.location.reload(), 800);
        }
      } catch {
        // 失敗時は何もしない
      }
    };

    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t) return;

      // Phase C レポート: 開く / 閉じる（全画面オーバーレイ）
      if (t.closest("[data-open-report]")) {
        e.preventDefault();
        const v = document.getElementById("phase-c-view");
        if (v) {
          v.style.display = "block";
          document.body.style.overflow = "hidden";
        }
        return;
      }
      if (t.closest("[data-close-report]")) {
        e.preventDefault();
        const v = document.getElementById("phase-c-view");
        if (v) {
          v.style.display = "none";
          document.body.style.overflow = "";
        }
        return;
      }

      const modeBtn = t.closest<HTMLElement>(".sb-mode-btn");
      if (modeBtn) {
        const mode = (modeBtn.dataset.mode as "paper" | "live") || "paper";
        // eslint-disable-next-line no-console
        console.log("[broker_mode] click", mode);
        handleBrokerModeToggle(mode);
        return;
      }

      const refresh = t.closest("#refresh-btn, #sb-refresh-btn");
      if (refresh) {
        doRefresh(refresh);
        return;
      }

      // v2.10: 約定ボタン → 手元DBに記録（楽天には触れない。mark_filled.py 経由）
      const markBtn = t.closest<HTMLElement>("[data-mark-filled]");
      if (markBtn) {
        e.preventDefault();
        if (markBtn.getAttribute("data-busy")) return;
        const did = markBtn.getAttribute("data-decision-id");
        const tk = markBtn.getAttribute("data-ticker") ?? "";
        if (!did) return;
        const card = markBtn.closest<HTMLElement>(".act.buy");
        const shEl = card?.querySelector<HTMLInputElement>("[data-shares-input]");
        const prEl = card?.querySelector<HTMLInputElement>("[data-price-input]");
        const sharesVal = shEl && shEl.value ? Number(shEl.value) : 0;
        const priceVal = prEl && prEl.value ? Number(prEl.value) : undefined;
        if (!sharesVal || sharesVal <= 0) {
          window.alert("約定した株数を入力してください（手入力）。");
          shEl?.focus();
          return;
        }
        const mode = document.body.classList.contains("live-mode-active")
          ? "live"
          : "paper";
        const ok = window.confirm(
          `${tk} を ${sharesVal}株${priceVal ? " @¥" + priceVal : ""} で約定記録しますか？\n（楽天には送信しません・手元DBに記録のみ・${mode}）`,
        );
        if (!ok) return;
        const label = markBtn.textContent;
        markBtn.setAttribute("data-busy", "1");
        markBtn.textContent = "記録中…";
        fetch("/api/mark-filled", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            decision_id: Number(did),
            broker_mode: mode,
            shares: sharesVal,
            price: priceVal,
          }),
        })
          .then((r) => r.json())
          .then((j) => {
            if (j?.ok) {
              markBtn.textContent = "✓ 記録済・保有へ反映中…";
              markBtn.setAttribute("disabled", "true");
              markBtn.closest(".act.buy")?.classList.add("filled");
              // 候補LLMを回さない軽量再生で口座/保有/決裁待ちを更新 → 完了後リロードで反映
              fetch("/api/refresh-holdings", { method: "POST" })
                .then(() => window.location.reload())
                .catch(() => window.location.reload());
            } else {
              markBtn.textContent = label || "✓ 約定";
              markBtn.removeAttribute("data-busy");
              // eslint-disable-next-line no-console
              console.error("[mark-filled] failed", j);
              window.alert("記録に失敗しました。再試行してください。");
            }
          })
          .catch((err) => {
            markBtn.textContent = label || "✓ 約定";
            markBtn.removeAttribute("data-busy");
            // eslint-disable-next-line no-console
            console.error("[mark-filled]", err);
            window.alert("記録に失敗しました。再試行してください。");
          });
        return;
      }

      // v2.10: 見送りボタン → 決定を cancelled（買わない）。楽天には触れない
      const skipBtn = t.closest<HTMLElement>("[data-skip-decision]");
      if (skipBtn) {
        e.preventDefault();
        if (skipBtn.getAttribute("data-busy")) return;
        const did = skipBtn.getAttribute("data-decision-id");
        const tk = skipBtn.getAttribute("data-ticker") ?? "";
        if (!did) return;
        if (
          !window.confirm(
            `${tk} を見送り（買わない）にしますか？\n候補から外れます（手元DBのみ）。`,
          )
        )
          return;
        skipBtn.setAttribute("data-busy", "1");
        skipBtn.textContent = "処理中…";
        fetch("/api/skip-decision", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision_id: Number(did) }),
        })
          .then((r) => r.json())
          .then((j) => {
            if (j?.ok) {
              skipBtn.closest(".act.buy")?.remove();
            } else {
              skipBtn.textContent = "見送り";
              skipBtn.removeAttribute("data-busy");
              window.alert("見送り処理に失敗しました。");
            }
          })
          .catch(() => {
            skipBtn.textContent = "見送り";
            skipBtn.removeAttribute("data-busy");
          });
        return;
      }

      // v2.10: 全約定ボタン → 株数を入力した買い候補をまとめて記録 → 保有反映
      const fillAll = t.closest<HTMLElement>("[data-fill-all]");
      if (fillAll) {
        e.preventDefault();
        if (fillAll.getAttribute("data-busy")) return;
        const targets: { id: number; shares: number; price?: number }[] = [];
        document
          .querySelectorAll<HTMLElement>(".buy-zone .act.buy")
          .forEach((c) => {
            const btn = c.querySelector<HTMLElement>("[data-mark-filled]");
            const sh = c.querySelector<HTMLInputElement>("[data-shares-input]");
            const pr = c.querySelector<HTMLInputElement>("[data-price-input]");
            const id = btn?.getAttribute("data-decision-id");
            const shares = sh && sh.value ? Number(sh.value) : 0;
            if (id && shares > 0 && !btn?.getAttribute("disabled")) {
              targets.push({
                id: Number(id),
                shares,
                price: pr && pr.value ? Number(pr.value) : undefined,
              });
            }
          });
        if (targets.length === 0) {
          window.alert(
            "株数を入力した買い候補がありません。各カードに約定株数を入力してください。",
          );
          return;
        }
        const mode = document.body.classList.contains("live-mode-active")
          ? "live"
          : "paper";
        if (
          !window.confirm(
            `${targets.length}件をまとめて約定記録しますか？\n（楽天には送信しません・手元DBのみ・${mode}）`,
          )
        )
          return;
        fillAll.setAttribute("data-busy", "1");
        fillAll.textContent = `記録中… (0/${targets.length})`;
        void (async () => {
          let done = 0;
          for (const tg of targets) {
            try {
              await fetch("/api/mark-filled", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  decision_id: tg.id,
                  broker_mode: mode,
                  shares: tg.shares,
                  price: tg.price,
                }),
              });
            } catch {
              // 続行（個別失敗は反映後に確認）
            }
            done++;
            fillAll.textContent = `記録中… (${done}/${targets.length})`;
          }
          fillAll.textContent = "反映中…";
          try {
            await fetch("/api/refresh-holdings", { method: "POST" });
          } catch {
            // 反映失敗でもリロードで再取得
          }
          window.location.reload();
        })();
        return;
      }

      const tab = t.closest<HTMLElement>(".topics-tab");
      if (tab) {
        const cat = tab.dataset.topicTab;
        document
          .querySelectorAll(".topics-tab")
          .forEach((x) => x.classList.remove("active"));
        tab.classList.add("active");
        document.querySelectorAll<HTMLElement>(".topic").forEach((topic) => {
          topic.style.display =
            cat === "all" || topic.dataset.cat === cat ? "" : "none";
        });
        return;
      }

      const toggle = t.closest<HTMLElement>(".more-toggle");
      if (toggle) {
        const id = toggle.dataset.toggle;
        const list = id
          ? document.querySelector(`[data-list="${id}"]`)
          : null;
        toggle.classList.toggle("open");
        list?.classList.toggle("open");
        return;
      }

      // v2.10: 決裁待ち decisions のオーバーフロー折りたたみのトグル
      const decisionsToggle = t.closest<HTMLElement>(
        "[data-decisions-overflow-toggle]",
      );
      if (decisionsToggle) {
        const overflow = decisionsToggle.parentElement?.querySelector<HTMLElement>(
          ".decisions-overflow",
        );
        if (overflow) {
          const wasCollapsed =
            overflow.dataset.decisionsOverflow === "collapsed";
          overflow.dataset.decisionsOverflow = wasCollapsed
            ? "expanded"
            : "collapsed";
          const icon = decisionsToggle.querySelector<HTMLElement>(".ovt-icon");
          const label = decisionsToggle.querySelector<HTMLElement>(".ovt-label");
          if (wasCollapsed) {
            if (icon) icon.textContent = "−";
            if (label) label.textContent = "決裁待ちを折りたたむ";
          } else {
            const cnt = overflow.querySelectorAll(".act.buy").length;
            if (icon) icon.textContent = "+";
            if (label)
              label.innerHTML = `残り <span class="decisions-overflow-count">${cnt}</span> 件の決裁待ちを表示`;
          }
        }
        return;
      }

      // v2.10: ネストしたオーバーフロー折りたたみのトグル
      const overflowToggle = t.closest<HTMLElement>("[data-overflow-toggle]");
      if (overflowToggle) {
        const inner = overflowToggle.closest<HTMLElement>(".more-list-inner");
        if (inner) {
          const wasCollapsed = inner.dataset.overflow === "collapsed";
          inner.dataset.overflow = wasCollapsed ? "expanded" : "collapsed";
          const icon = overflowToggle.querySelector<HTMLElement>(".ovt-icon");
          const label = overflowToggle.querySelector<HTMLElement>(".ovt-label");
          if (wasCollapsed) {
            if (icon) icon.textContent = "−";
            if (label) label.textContent = "折りたたむ";
          } else {
            const overflow =
              Array.from(inner.querySelectorAll(".mini-act")).length - 4;
            if (icon) icon.textContent = "+";
            if (label)
              label.innerHTML = `残り <span data-overflow-count>${overflow}</span> 件を表示`;
          }
        }
        return;
      }
    };

    // v2.10: 初期化 — 各 .more-list-inner の .mini-act 数が 5 以上ならトグル表示
    const refreshOverflowToggles = () => {
      document
        .querySelectorAll<HTMLElement>(".more-list-inner")
        .forEach((inner) => {
          const total = inner.querySelectorAll(".mini-act").length;
          const btn = inner.querySelector<HTMLElement>(
            "[data-overflow-toggle]",
          );
          if (!btn) return;
          if (total <= 4) {
            btn.hidden = true;
            return;
          }
          btn.hidden = false;
          if (inner.dataset.overflow === "collapsed") {
            const count = inner.querySelector<HTMLElement>(
              "[data-overflow-count]",
            );
            if (count) count.textContent = String(total - 4);
          }
        });
    };
    refreshOverflowToggles();
    // LiveData が動的に .mini-act を追加することがあるので、少し遅延して再計算
    const overflowReinit = window.setTimeout(refreshOverflowToggles, 1500);

    const onOver = (e: MouseEvent) => {
      const t = e.target as Element | null;
      const pt = t?.closest<HTMLElement>(".hover-point, .track-point");
      if (!pt) return;
      const d = pt.dataset;
      if (pt.classList.contains("hover-point")) {
        const actual = d.actual ?? "";
        const cls =
          actual.startsWith("-")
            ? "down"
            : actual.startsWith("+") && actual !== "+0%"
              ? "up"
              : "";
        const ticker =
          pt.closest<HTMLElement>(".hold-graph")?.dataset.graph ?? "";
        tooltip.innerHTML = `
          <div class="tooltip-row"><span class="lbl">${ticker} · ${d.date ?? ""}</span></div>
          <div class="tooltip-row"><span class="lbl">実績</span><span class="val ${cls}">${actual}</span></div>
          <div class="tooltip-row"><span class="lbl">予測</span><span class="val key">${d.pred ?? ""}</span></div>`;
      } else {
        const hit = d.hit;
        const cls = hit === "hit" ? "up" : hit === "miss" ? "down" : hit === "now" ? "key" : "";
        tooltip.innerHTML = `
          <div class="tooltip-row"><span class="lbl">${d.date ?? ""}</span><span class="val">${d.ticker ?? ""}</span></div>
          <div class="tooltip-row"><span class="lbl">アクション</span><span class="val">${d.action ?? ""}</span></div>
          <div class="tooltip-row"><span class="lbl">結果</span><span class="val ${cls}">${d.result ?? ""}</span></div>`;
      }
      tooltip.classList.add("show");
    };

    const onMove = (e: MouseEvent) => {
      if (tooltip.classList.contains("show")) positionTooltip(e);
    };

    const onOut = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (t?.closest(".hover-point, .track-point")) {
        tooltip.classList.remove("show");
      }
    };

    document.addEventListener("click", onClick);
    document.addEventListener("mouseover", onOver);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseout", onOut);

    return () => {
      window.clearInterval(interval);
      window.clearTimeout(overflowReinit);
      document.removeEventListener("click", onClick);
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseout", onOut);
      tooltip.remove();
    };
  }, []);

  return null;
}
