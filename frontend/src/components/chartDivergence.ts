import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

type Indicator = "macd" | "kdj";
type Pivot = { date: string; price: number; indicator: number };
type Divergence = {
  kind: "top" | "bottom";
  previous: Pivot;
  current: Pivot;
  confirmedOn: string;
};

const WING = 2;
const MAX_PIVOT_DISTANCE = 60;

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

export function axisPosition(value: unknown, level: number) {
  return !finite(value)
    ? "—"
    : value > level
      ? "轴上"
      : value < level
        ? "轴下"
        : "在轴上";
}

export function chartDivergences(
  bars: Row[],
  gapDates: Set<string>,
  period: ChartPeriod,
  indicator: Indicator,
) {
  const field = indicator === "macd" ? "macd_dif" : "kdj_k";
  const result = new Map<string, Divergence[]>();
  const latest: Partial<Record<Divergence["kind"], Pivot & { index: number }>> =
    {};
  let segmentStart = 0;
  bars.forEach((bar, index) => {
    const value = bar[field] ?? bar.indicators?.[field];
    const previous = bars[index - 1];
    const evidence =
      period === "D"
        ? bar.missing_market_sessions_before
        : period === "W"
          ? bar.weekly_missing_market_sessions
          : bar.monthly_missing_market_sessions;
    const comparable =
      finite(value) &&
      finite(bar.high) &&
      finite(bar.low) &&
      bar.low > 0 &&
      bar.high >= bar.low &&
      bar.complete !== false &&
      !bar.noon_provisional &&
      !bar.intraday_provisional &&
      (period === "D" || bar.complete === true);
    if (
      !comparable ||
      gapDates.has(bar.date) ||
      evidence > 0 ||
      (previous && bar.date <= previous.date) ||
      bar.indicator_reset_reason ||
      bar.display_indicator_reset_reason
    ) {
      segmentStart = comparable ? index : index + 1;
      delete latest.top;
      delete latest.bottom;
    }
    if (index - segmentStart < WING * 2) return;
    const center = index - WING;
    const pivot = bars[center];
    const neighbors = bars
      .slice(center - WING, index + 1)
      .filter((_, i) => i !== WING);
    for (const kind of ["top", "bottom"] as const) {
      const priceField = kind === "top" ? "high" : "low";
      const price = pivot[priceField];
      const oscillator = pivot[field] ?? pivot.indicators?.[field];
      const extreme = neighbors.every((neighbor) =>
        kind === "top"
          ? price > neighbor[priceField]
          : price < neighbor[priceField],
      );
      if (!extreme) continue;
      const prior = latest[kind];
      const current = { date: pivot.date, price, indicator: oscillator };
      latest[kind] = { ...current, index: center };
      if (!prior || center - prior.index > MAX_PIVOT_DISTANCE) continue;
      if (
        kind === "top"
          ? price <= prior.price || oscillator >= prior.indicator
          : price >= prior.price || oscillator <= prior.indicator
      )
        continue;
      const { date, price: priorPrice, indicator: priorOscillator } = prior;
      const event = {
        kind,
        previous: { date, price: priorPrice, indicator: priorOscillator },
        current,
        confirmedOn: bar.date,
      };
      result.set(pivot.date, [...(result.get(pivot.date) || []), event]);
    }
  });
  return result;
}
