import { Badge } from "../../components/UI";
import { percent, tone } from "../../lib/format";
import { noonNumber } from "./noonPresentation";
import "./NoonStockChange.css";

const markers = {
  limit_up: { label: "涨停", tone: "danger" },
  limit_down: { label: "跌停", tone: "good" },
  limit_break: { label: "炸板", tone: "warn" },
} as const;

/** State comes from the same noon snapshot's verified source pool. */
export default function NoonStockChange({
  value,
  state,
}: {
  value: unknown;
  state?: unknown;
}) {
  const change = noonNumber(value);
  const marker =
    state === "limit_up" || state === "limit_down" || state === "limit_break"
      ? markers[state]
      : null;
  return (
    <span className="noon-stock-change">
      <span className={tone(change)}>{percent(change)}</span>
      {marker && (
        <span title={`本次午间来源池已核验${marker.label}，收盘状态另行核验`}>
          <Badge tone={marker.tone}>{marker.label}</Badge>
        </span>
      )}
    </span>
  );
}
