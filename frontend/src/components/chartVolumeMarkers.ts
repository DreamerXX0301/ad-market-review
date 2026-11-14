import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

const LOOKBACK = 20;
const DAY_MS = 86_400_000;

type VolumePoint = {
  label: string | null;
  kind: "expansion" | "low" | null;
  previousRatio: number | null;
  averageRatio: number | null;
};

function periodNumber(date: string, period: "W" | "M"): number {
  if (period === "M") return +date.slice(0, 4) * 12 + +date.slice(5, 7);
  // Jan 5, 1970 was a Monday. Group by calendar week even when a holiday
  // means that successive bars end on different weekdays.
  return Math.floor((Date.parse(date) / DAY_MS - 4) / 7);
}

export function chartVolumeMarkers(
  bars: Row[],
  period: ChartPeriod,
  quoteGaps: Row[] = [],
) {
  const gaps = new Set(quoteGaps.map((gap) => gap.resume_quote_date));
  const points = new Map<string, VolumePoint>();
  const recent: number[] = [];
  let previous: Row | undefined;
  for (const bar of bars) {
    if (
      previous &&
      (bar.date <= previous.date ||
        (period === "D"
          ? gaps.has(bar.date) ||
            bar.missing_market_sessions_before > 0 ||
            (Number.isInteger(bar.trading_days_to_as_of) &&
              Number.isInteger(previous.trading_days_to_as_of) &&
              previous.trading_days_to_as_of !== bar.trading_days_to_as_of + 1)
          : periodNumber(bar.date, period) -
              periodNumber(previous.date, period) !==
            1) ||
        (bar.source && previous.source && bar.source !== previous.source))
    ) {
      recent.length = 0;
    }
    previous = bar;
    const volume = bar.volume;
    if (
      typeof volume !== "number" ||
      !Number.isFinite(volume) ||
      volume <= 0 ||
      bar.noon_provisional ||
      bar.intraday_provisional ||
      bar.complete === false ||
      (period === "W" &&
        (bar.week_closed !== true ||
          bar.weekly_quote_coverage_complete !== true ||
          bar.complete !== true)) ||
      (period === "M" &&
        (bar.month_closed !== true ||
          bar.monthly_quote_coverage_complete !== true ||
          bar.complete !== true))
    ) {
      recent.length = 0;
      continue;
    }
    const lastVolume = recent.at(-1);
    const previousRatio =
      lastVolume != null && Number.isFinite(volume / lastVolume)
        ? volume / lastVolume
        : null;
    const averageRatio =
      recent.length === LOOKBACK
        ? (volume * LOOKBACK) / recent.reduce((sum, value) => sum + value, 0)
        : null;
    const low = recent.length === LOOKBACK && volume < Math.min(...recent);
    const multiple = previousRatio == null ? 0 : Math.floor(previousRatio);
    points.set(bar.date, {
      label: low
        ? "地量"
        : multiple >= 3
          ? "3倍+"
          : multiple >= 2
            ? "2倍量"
            : null,
      kind: low ? "low" : multiple >= 2 ? "expansion" : null,
      previousRatio,
      averageRatio,
    });
    if (recent.length === LOOKBACK) recent.shift();
    recent.push(volume);
  }
  return points;
}
