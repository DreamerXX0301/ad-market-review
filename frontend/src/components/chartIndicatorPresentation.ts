import { isHiddenIndicatorField } from "../lib/indicatorVisibility";

type RecordValue = Record<string, unknown>;

export const chartIndicatorLabel = (field: string): string =>
  isHiddenIndicatorField(field)
    ? ""
    : (
        {
          macd_dif: "DIF",
          macd_dea: "DEA",
          macd_hist: "MACD柱",
          kdj_k: "K",
          kdj_d: "D",
        } as Record<string, string>
      )[field] || field.toUpperCase();

/** A zero is a valid indicator value; missing display values need their cause. */
export function chartIndicatorAvailability(
  bar: RecordValue | undefined,
  fields: string[],
  period: "D" | "W" | "M",
) {
  const missing = fields.filter(
    (field) =>
      !isHiddenIndicatorField(field) &&
      (typeof bar?.[field] !== "number" || !Number.isFinite(bar[field])),
  );
  if (!bar || !missing.length) return { missing, label: "", reason: "" };
  const forming =
    period === "M"
      ? bar.month_closed === false
      : period === "W" && bar.week_closed === false;
  if (forming) {
    const unit = period === "M" ? "月" : "周";
    const historicalReason =
      bar.display_indicator_warmup_reason ??
      bar.indicator_warmup_reason ??
      bar.indicator_reset_reason;
    return {
      missing,
      label: `本${unit}形成中`,
      reason: `本${unit}尚未结束，不用当前形成K判定完整周期信号；历史预热以最近已完成周期为准。${typeof historicalReason === "string" ? historicalReason : ""}`,
    };
  }
  const required = Math.max(
    ...missing.map((field) =>
      /^ma\d+$/.test(field) ? Number(field.slice(2)) : 35,
    ),
  );
  const count = bar.display_indicator_warmup_bars ?? bar.indicator_warmup_bars;
  const reason = String(
    bar.display_indicator_warmup_reason ??
      bar.indicator_warmup_reason ??
      bar.indicator_reset_reason ??
      "报价、完整周期或指标预热尚未满足",
  );
  const unit =
    period === "D" ? "根报价" : period === "W" ? "根完整周K" : "根完整月K";
  const hasProgress =
    typeof count === "number" &&
    Number.isFinite(count) &&
    count >= 0 &&
    count < required;
  return {
    missing,
    label: hasProgress ? `预热 ${count}/${required}` : "待核验",
    reason: `${reason}${hasProgress ? `；需要${required}${unit}，当前${count}根` : ""}`,
  };
}
