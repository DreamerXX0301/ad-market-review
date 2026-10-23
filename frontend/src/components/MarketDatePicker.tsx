import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { useMemo } from "react";
import DatePicker from "./DatePicker";
import { adjacentMarketDate, retainedMarketDates } from "./marketDate";
import "./MarketDatePicker.css";

/** Session navigation and all other date fields share one calendar panel. */
export default function MarketDatePicker({
  value,
  dates,
  onChange,
}: {
  value: string;
  dates: string[];
  onChange: (date: string) => void;
}) {
  const retained = useMemo(() => retainedMarketDates(dates), [dates]);
  const previous = adjacentMarketDate(retained, value, -1),
    next = adjacentMarketDate(retained, value, 1),
    newest = retained.at(-1) || "";
  return (
    <div className="market-date-picker">
      <button
        type="button"
        className="market-date-step"
        aria-label="前一交易日"
        title={previous ? `前一交易日 ${previous}` : "已到最早留存交易日"}
        disabled={!previous}
        onClick={() => previous && onChange(previous)}
      >
        <ChevronLeft size={14} />
      </button>
      <DatePicker
        value={value}
        allowedDates={retained}
        onChange={onChange}
        compact
        ariaLabel="盘面观察日期"
        title="盘面观察日"
        help="仅改变盘面研究，策略与跟踪保持独立"
        shortcut={{ label: "回到最新", date: newest }}
      />
      <button
        type="button"
        className="market-date-step"
        aria-label="后一交易日"
        title={next ? `后一交易日 ${next}` : "已到最新留存交易日"}
        disabled={!next}
        onClick={() => next && onChange(next)}
      >
        <ChevronRight size={14} />
      </button>
      <button
        type="button"
        className="market-date-step market-date-latest"
        aria-label="回到最新交易日"
        title={newest ? `回到最新交易日 ${newest}` : "暂无已留存交易日"}
        disabled={!newest || value === newest}
        onClick={() => newest && onChange(newest)}
      >
        <RotateCcw size={13} />
      </button>
    </div>
  );
}
