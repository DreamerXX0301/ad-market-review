import { Activity, BellRing, History } from "lucide-react";
import "./Tracking.css";

export default function TrackingTabs({
  value,
  onChange,
  counts,
  activeKey = "active",
  historyKey = "history",
}: {
  value: string;
  onChange: (value: string) => void;
  counts: { active?: number; alerts?: number; history?: number };
  activeKey?: string;
  historyKey?: string;
}) {
  const tabs = [
    { key: activeKey, label: "跟踪中", count: counts.active, icon: Activity },
    { key: "alerts", label: "触发提醒", count: counts.alerts, icon: BellRing },
    {
      key: historyKey,
      label: "结束历史",
      count: counts.history,
      icon: History,
    },
  ];
  return (
    <div
      className="tracking-view-tabs"
      role="tablist"
      aria-label="跟踪记录分类"
    >
      {tabs.map(({ key, label, count, icon: Icon }, index) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          tabIndex={value === key ? 0 : -1}
          className={value === key ? "active" : ""}
          onClick={() => onChange(key)}
          onKeyDown={(event) => {
            const target =
              event.key === "ArrowRight"
                ? (index + 1) % tabs.length
                : event.key === "ArrowLeft"
                  ? (index + tabs.length - 1) % tabs.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : null;
            if (target === null) return;
            event.preventDefault();
            onChange(tabs[target].key);
            event.currentTarget.parentElement
              ?.querySelectorAll("button")
              [target]?.focus();
          }}
        >
          <Icon size={17} aria-hidden="true" />
          <span>{label}</span>
          <strong>{count == null ? "—" : count.toLocaleString()}</strong>
        </button>
      ))}
    </div>
  );
}
