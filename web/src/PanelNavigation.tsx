export type PanelKind = "tasks" | "data";

export function PanelNavigation({ active, needsCapture, onChange }: { active: PanelKind; needsCapture: boolean; onChange: (panel: PanelKind) => void }) {
  return <nav className="panel-navigation" aria-label="控制台面板">
    <button className={active === "tasks" ? "active" : ""} aria-current={active === "tasks" ? "page" : undefined} onClick={() => onChange("tasks")}>任务面板</button>
    <button className={active === "data" ? "active" : ""} aria-current={active === "data" ? "page" : undefined} onClick={() => onChange("data")}>数据面板{needsCapture && <i title="今天尚未采集" />}</button>
  </nav>;
}
