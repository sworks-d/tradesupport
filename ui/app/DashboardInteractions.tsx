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
      window.setTimeout(() => {
        simulatePriceUpdate();
        btn.classList.remove("spinning");
      }, 700);
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

    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t) return;

      const refresh = t.closest("#refresh-btn, #sb-refresh-btn");
      if (refresh) {
        doRefresh(refresh);
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
    };

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
      document.removeEventListener("click", onClick);
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseout", onOut);
      tooltip.remove();
    };
  }, []);

  return null;
}
