import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

/** Consume same-basis server evidence, never adjacent visible or hovered bars. */
export function candleExtremeChange(bar: Row | undefined, period: ChartPeriod) {
  const cycle = period === "D" ? "当日" : period === "W" ? "周内" : "月内";
  const reference = bar?.chart_extreme_change_reference_date;
  const validDate = (value: unknown): value is string => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
      return false;
    const parsed = new Date(value + "T00:00:00Z");
    return (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  };
  const dated =
    validDate(reference) && validDate(bar?.date) && reference < bar.date;
  const compatible =
    bar?.chart_extreme_change_period === period &&
    bar?.price_continuity !== false &&
    bar?.corporate_action !== true &&
    dated;
  const value = (side: "high" | "low") => {
    const number = bar?.[`chart_${side}_change_pct`];
    const known =
      compatible && typeof number === "number" && Number.isFinite(number);
    return {
      value: known ? number : null,
      label: `${cycle}${side === "high" ? "最高" : "最低"}涨幅`,
      reason:
        bar?.[`chart_${side}_change_pct_reason`] ||
        "缺少本周期同一价格口径的前收比较证据，最高/最低涨幅保持未知",
    };
  };
  return { high: value("high"), low: value("low") };
}
