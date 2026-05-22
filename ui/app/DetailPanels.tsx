"use client";

import { useEffect } from "react";

/**
 * 売り買い詳細パネル（.detail-panel[data-panel]）の開閉を配線する。
 * マークアップが dangerouslySetInnerHTML の生DOMのため、イベント委譲で実装。
 * - [data-detail] クリック → 対応パネルと背景を表示
 * - .detail-close-btn / #detail-backdrop クリック・Escape → 閉じる
 * F4（インタラクション）の先取り。F2のコンポーネント化時に React state へ移す。
 */
export default function DetailPanels() {
  useEffect(() => {
    const backdrop = document.getElementById("detail-backdrop");

    const closePanels = () => {
      document
        .querySelectorAll(".detail-panel.show")
        .forEach((p) => p.classList.remove("show"));
      backdrop?.classList.remove("show");
    };

    const openPanel = (panelId: string) => {
      closePanels();
      const panel = document.querySelector(`[data-panel="${panelId}"]`);
      if (panel) {
        panel.classList.add("show");
        backdrop?.classList.add("show");
      }
    };

    const onClick = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (!target) return;
      if (target.closest(".detail-close-btn") || target.id === "detail-backdrop") {
        closePanels();
        return;
      }
      // more-toggle（他の候補/他の注視）の展開はパネルを開かない
      if (target.closest(".more-toggle")) return;
      const trigger = target.closest<HTMLElement>("[data-detail]");
      if (trigger?.dataset.detail) openPanel(trigger.dataset.detail);
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanels();
    };

    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return null;
}
