"use client";

import { useEffect } from "react";

/**
 * チャートSVG（preserveAspectRatio="none" でフル幅に伸びるもの）の
 * 文字(<text>) とマーカー(<circle>/<ellipse>) を、SVGの非均等スケールに対して
 * 逆スケールし、サイズを一定（歪まない・大きさ不変）に保つ。
 * 線/点線の太さは CSS の vector-effect:non-scaling-stroke で別途固定済み。
 * これにより「左右はフル幅で可変、文字と線の太さは不変」を実現する。
 */
export default function ChartCrispLabels() {
  useEffect(() => {
    const svgs = Array.from(
      document.querySelectorAll<SVGSVGElement>(
        'svg[preserveAspectRatio="none"]',
      ),
    );
    if (svgs.length === 0) return;

    const num = (el: Element, a: string) =>
      parseFloat(el.getAttribute(a) || "0");

    const apply = (svg: SVGSVGElement) => {
      const vb = svg.viewBox.baseVal;
      const r = svg.getBoundingClientRect();
      if (!vb || !vb.width || !vb.height || !r.width || !r.height) return;
      const sx = r.width / vb.width;
      const sy = r.height / vb.height;
      if (!isFinite(sx) || !isFinite(sy) || !sx || !sy) return;
      // アンカー点 (cx,cy) を固定したまま、SVGスケールを打ち消す
      const t = (cx: number, cy: number) =>
        `translate(${cx} ${cy}) scale(${1 / sx} ${1 / sy}) translate(${-cx} ${-cy})`;

      svg.querySelectorAll<SVGTextElement>("text").forEach((el) =>
        el.setAttribute("transform", t(num(el, "x"), num(el, "y"))),
      );
      svg
        .querySelectorAll<SVGCircleElement>("circle, ellipse")
        .forEach((el) => {
          if (el.classList.contains("now-pulse")) return; // CSSアニメと競合回避
          el.setAttribute("transform", t(num(el, "cx"), num(el, "cy")));
        });
    };

    const ro = new ResizeObserver((entries) =>
      entries.forEach((e) => apply(e.target as SVGSVGElement)),
    );
    svgs.forEach((svg) => {
      apply(svg);
      ro.observe(svg);
    });
    return () => ro.disconnect();
  }, []);

  return null;
}
