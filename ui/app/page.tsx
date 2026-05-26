import { readFileSync } from "node:fs";
import { join } from "node:path";
import ChartCrispLabels from "./ChartCrispLabels";
import DashboardInteractions from "./DashboardInteractions";
import DetailPanels from "./DetailPanels";
import DisciplineBanner from "./DisciplineBanner";
import LiveData from "./LiveData";

// F1（逐語移植）: new_dashboard.html の <body> 内マークアップを生HTMLのまま注入し、
// CSS・フォント・DOM が元と同一に描画されることを視覚回帰で保証する。
// JSX への変換ドリフトを避けるための一時措置。F2 で順次コンポーネント化する。
// display:contents の wrapper はボックスを生成せず、.app グリッドを body 直下相当に保つ。
//
// DisciplineBanner は D-24 / D-25 / X-2 を反映する追加層（既存 HTML は無改変）。
// ダッシュボードより前に描画して「朝5分まず posture を見る」原則に揃える。
export default function Page() {
  const markup = readFileSync(
    join(process.cwd(), "app", "dashboard.html"),
    "utf8",
  );
  return (
    <>
      <DisciplineBanner />
      <div
        style={{ display: "contents" }}
        dangerouslySetInnerHTML={{ __html: markup }}
      />
      <ChartCrispLabels />
      <DetailPanels />
      <DashboardInteractions />
      <LiveData />
    </>
  );
}
