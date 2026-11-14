import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

type Oscillator = "macd" | "kdj";
type IndicatorCross = { date: string; value: number; rising: boolean };

const fields = {
  macd: ["macd_dif", "macd_dea"],
  kdj: ["kdj_k", "kdj_d"],
} as const;

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function comparable(bar: Row, period: ChartPeriod) {
  return (
    !bar.noon_provisional &&
    !bar.intraday_provisional &&
    (period === "D" ? bar.complete !== false : bar.complete === true) &&
    (period !== "W" || bar.weekly_quote_coverage_complete !== false) &&
    (period !== "M" ||
      (bar.monthly_quote_coverage_complete !== false &&
        bar.monthly_price_comparable !== false))
  );
}

export function chartIndicatorCrosses(
  bars: Row[],
  gapDates: ReadonlySet<string>,
  period: ChartPeriod,
  oscillator: Oscillator,
): IndicatorCross[] {
  const [fastField, slowField] = fields[oscillator];
  const points: IndicatorCross[] = [];
  let previous: { date: string; fast: number; slow: number } | null = null;
  for (const bar of bars) {
    const fast = bar[fastField] ?? bar.indicators?.[fastField];
    const slow = bar[slowField] ?? bar.indicators?.[slowField];
    if (!comparable(bar, period) || !finite(fast) || !finite(slow)) {
      previous = null;
      continue;
    }
    const gap =
      period === "D"
        ? gapDates.has(bar.date) || bar.missing_market_sessions_before > 0
        : period === "W"
          ? bar.weekly_missing_market_sessions > 0 ||
            bar.weekly_continuity_reset_reason
          : bar.monthly_missing_market_sessions > 0 ||
            bar.monthly_continuity_reset_reason;
    if (
      previous &&
      bar.date > previous.date &&
      !gap &&
      !bar.indicator_reset_reason &&
      !bar.display_indicator_reset_reason
    ) {
      const rising = previous.fast <= previous.slow && fast > slow;
      const falling = previous.fast >= previous.slow && fast < slow;
      if (rising || falling)
        points.push({ date: bar.date, value: fast, rising });
    }
    previous = { date: bar.date, fast, slow };
  }
  return points;
}
