import type { Row } from "../lib/api";

const indicatorFields = [
  "ma5",
  "ma10",
  "ma20",
  "ma30",
  "ma60",
  "ma250",
  "macd_dif",
  "macd_dea",
  "macd_hist",
  "kdj_k",
  "kdj_d",
  "kdj_j",
];

export function chartProvisionalIndicators(data: Row | null | undefined) {
  const preview = data?.provisional_indicators;
  const last = Array.isArray(data?.bars) ? data.bars.at(-1) : undefined;
  const forming =
    data?.period === "W"
      ? last?.week_closed === false
      : data?.period === "M" && last?.month_closed === false;
  if (
    !forming ||
    last?.complete !== false ||
    !preview ||
    preview.date !== data?.as_of ||
    preview.date !== last.date ||
    !Number.isSafeInteger(preview.warmup_bars) ||
    preview.warmup_bars < 1 ||
    !preview.values ||
    typeof preview.values !== "object"
  )
    return null;
  return {
    date: preview.date as string,
    warmupBars: preview.warmup_bars as number,
    values: Object.fromEntries(
      indicatorFields.map((field) => {
        const value = preview.values[field];
        return [
          field,
          typeof value === "number" && Number.isFinite(value) ? value : null,
        ];
      }),
    ),
  };
}
