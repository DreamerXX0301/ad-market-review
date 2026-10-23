import { CheckCircle2, CircleHelp, Clock3, MinusCircle } from "lucide-react";
import {
  nextDecisionTab,
  strategyDecisionCounts,
} from "./strategyResultPresentation";
import type { Row } from "../../lib/api";
import "./StrategyResultTabs.css";

const tabs = [
  { key: "matched", label: "满足", icon: CheckCircle2 },
  { key: "unknown", label: "待核验", icon: CircleHelp },
  { key: "unmatched", label: "未满足", icon: MinusCircle },
];
/** One compact result filter for stock and sector runs; zero is a real count. */
export default function StrategyResultTabs({
  run,
  value,
  onChange,
}: {
  run: Row;
  value: string;
  onChange: (value: string) => void;
}) {
  const counts = strategyDecisionCounts(run);
  return (
    <div
      className="strategy-result-tabs"
      role="tablist"
      aria-label="策略结果分类"
    >
      {tabs.map(({ key, label, icon: Icon }, index) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          tabIndex={value === key ? 0 : -1}
          className={`${key} ${value === key ? "active" : ""}`}
          onClick={() => onChange(key)}
          onKeyDown={(event) => {
            const next = nextDecisionTab(index, event.key);
            if (next === null) return;
            event.preventDefault();
            onChange(tabs[next].key);
            event.currentTarget.parentElement
              ?.querySelectorAll("button")
              [next]?.focus();
          }}
        >
          <Icon size={15} aria-hidden="true" />
          <span>{label}</span>
          <b>
            {counts[key as keyof typeof counts] === null
              ? "—"
              : counts[key as keyof typeof counts]?.toLocaleString()}
          </b>
        </button>
      ))}
    </div>
  );
}

export function StrategyRunNotice({ notice }: { notice: Row | null }) {
  return notice?.attention ? (
    <div
      className={`strategy-result-note ${notice.stale ? "stale" : ""}`}
      role="status"
      title={notice.context}
    >
      <Clock3 size={13} aria-hidden="true" />
      <span>{notice.message}</span>
    </div>
  ) : null;
}
