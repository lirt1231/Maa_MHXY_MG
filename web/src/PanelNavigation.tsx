export type PanelKind = "tasks" | "data";

export function PanelNavigation({ active, needsCapture, onChange }: { active: PanelKind; needsCapture: boolean; onChange: (panel: PanelKind) => void }) {
  return <nav className="panel-navigation" aria-label="控制台面板">
    <button className={active === "tasks" ? "active" : ""} aria-current={active === "tasks" ? "page" : undefined} onClick={() => onChange("tasks")}>任务中心</button>
    <button className={active === "data" ? "active" : ""} aria-current={active === "data" ? "page" : undefined} onClick={() => onChange("data")}>我的收益{needsCapture && <i title="今天还没有记录金币和经验" />}</button>
  </nav>;
}
