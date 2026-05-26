import { readFileSync } from "node:fs";
import { join } from "node:path";
import CashFlowPanel from "./CashFlowPanel";
import ChartCrispLabels from "./ChartCrispLabels";
import DashboardInteractions from "./DashboardInteractions";
import DetailPanels from "./DetailPanels";
import LiveData from "./LiveData";
import ZeelePanel from "./ZeelePanel";

// F1（逐語移植）: new_dashboard.html の <body> 内マークアップを生HTMLのまま注入し、
// CSS・フォント・DOM が元と同一に描画されることを視覚回帰で保証する。
// JSX への変換ドリフトを避けるための一時措置。F2 で順次コンポーネント化する。
// display:contents の wrapper はボックスを生成せず、.app グリッドを body 直下相当に保つ。
//
// ZeelePanel は **左から** スライドアウトする攻めレコメンドパネル（D-24/D-25 反映）。
// 既存 DetailPanels（右から）と鏡像対称で二車線構造を画面で表現する。
// 既存 dashboard.html は無改変（D-19 ガード）。
export default function Page() {
  const markup = readFileSync(
    join(process.cwd(), "app", "dashboard.html"),
    "utf8",
  );
  return (
    <>
      <div
        style={{ display: "contents" }}
        dangerouslySetInnerHTML={{ __html: markup }}
      />
      <ChartCrispLabels />
      <DetailPanels />
      <DashboardInteractions />
      <LiveData />
      <ZeelePanel />
      <CashFlowPanel />
    </>
  );
}
