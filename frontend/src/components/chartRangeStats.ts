import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";
import { chartSessionDistance } from "./chartSessionDistance";
import { chartSinceReturn } from "./chartSinceReturn";

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

export function rangeEndpoint(bar: Row, period: ChartPeriod) {
  return (
    positive(bar.close) &&
    !bar.noon_provisional &&
    !bar.intraday_provisional &&
    (period === "D" ? bar.complete !== false : bar.complete === true) &&
    (period !== "W" || bar.weekly_quote_coverage_complete !== false) &&
    (period !== "M" || bar.monthly_quote_coverage_complete !== false)
  );
}

export function chartRangeStats(
  data: Row,
  dates: [string, string],
  period: ChartPeriod,
) {
  const [start, end] = [...dates].sort();
  const selected: Row[] = data.bars.filter(
    (bar: Row) => bar.date >= start && bar.date <= end,
  );
  const first = selected[0];
  const last = selected.at(-1);
  if (
    !first ||
    !last ||
    first.date !== start ||
    last.date !== end ||
    start === end ||
    !rangeEndpoint(first, period) ||
    !rangeEndpoint(last, period)
  )
    return null;
  const change = chartSinceReturn(first, data, end);
  const startDistance = chartSessionDistance(first, data).days;
  const endDistance = chartSessionDistance(last, data).days;
  const sessions =
    startDistance != null && endDistance != null && startDistance > endDistance
      ? startDistance - endDistance
      : null;
  const gaps =
    (period === "D" && sessions != null && selected.length !== sessions + 1) ||
    (data.coverage?.missing_dates || []).some(
      (date: string) => date > start && date <= end,
    ) ||
    (data.quote_gaps || []).some(
      (gap: Row) => gap.start_date <= end && gap.end_date > start,
    ) ||
    selected.some(
      (bar, index) =>
        !rangeEndpoint(bar, period) ||
        (index > 0 &&
          (bar.date <= selected[index - 1].date ||
            bar.missing_market_sessions_before > 0)),
    );
  let high: number | null = null;
  let low: number | null = null;
  let volume: number | null = gaps ? null : 0;
  let turnover: number | null = gaps ? null : 0;
  if (
    !gaps &&
    change.value != null &&
    selected.every(
      (bar) => positive(bar.high) && positive(bar.low) && bar.high >= bar.low,
    )
  ) {
    high = selected.reduce((max, bar) => Math.max(max, bar.high), first.high);
    low = selected.reduce((min, bar) => Math.min(min, bar.low), first.low);
  }
  for (const bar of selected) {
    volume =
      volume != null && (bar.volume === 0 || positive(bar.volume))
        ? volume + bar.volume
        : null;
    turnover =
      turnover != null && (bar.amount === 0 || positive(bar.amount))
        ? turnover + bar.amount
        : null;
  }
  return {
    start,
    end,
    change,
    high,
    low,
    volume: Number.isFinite(volume) ? volume : null,
    turnover: Number.isFinite(turnover) ? turnover : null,
    count: selected.length,
    gaps,
    sessions,
  };
}
