import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { createContext, useMemo } from "react";
import DatePicker from "./DatePicker";
import { adjacentMarketDate, retainedMarketDates } from "./marketDate";
import "./MarketDatePicker.css";

export const ChartMarketDatesContext = createContext<string[]>([]);

/** Session navigation and all other date fields share one calendar panel. */
export default function MarketDatePicker({
  value,
  dates,
  onChange,
  onLatest,
  fixed = false,
  scope = "market",
}: {
  value: string;
  dates: string[];
  onChange: (date: string) => void;
  onLatest?: () => void;
  fixed?: boolean;
  scope?: "market" | "chart";
}) {
  const retained = useMemo(() => retainedMarketDates(dates), [dates]);
  const previous = adjacentMarketDate(retained, value, -1),
    next = adjacentMarketDate(retained, value, 1),
    newest = retained.at(-1) || "";
  const chart = scope === "chart";
  return (
    <div
      className={`market-date-picker${chart ? " chart-date-picker" : ""}`}
      role={chart ? "group" : undefined}
      aria-label={chart ? "切换图表交易日" : undefined}
    >
      <button
        type="button"
        className="market-date-step"
        aria-label={chart ? "图表前一交易日" : "前一交易日"}
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
        ariaLabel={chart ? "图表观察日期" : "盘面观察日期"}
        title={chart ? "跳转图表日期" : "盘面观察日"}
        help={
          chart
            ? "选择已发布交易日，仅切换当前图表"
            : "仅改变盘面研究，策略与跟踪保持独立"
        }
        shortcut={{ label: "回到最新", date: newest }}
        onShortcut={onLatest}
        shortcutActive={fixed}
        selectCurrent
      />
      <button
        type="button"
        className="market-date-step"
        aria-label={chart ? "图表后一交易日" : "后一交易日"}
        title={next ? `后一交易日 ${next}` : "已到最新留存交易日"}
        disabled={!next}
        onClick={() => next && onChange(next)}
      >
        <ChevronRight size={14} />
      </button>
      <button
        type="button"
        className="market-date-step market-date-latest"
        aria-label={chart ? "图表回到最新交易日" : "回到最新交易日"}
        title={newest ? `回到最新交易日 ${newest}` : "暂无已留存交易日"}
        disabled={!newest || (value === newest && !fixed)}
        onClick={() => newest && (onLatest ? onLatest() : onChange(newest))}
      >
        <RotateCcw size={13} />
      </button>
    </div>
  );
}
