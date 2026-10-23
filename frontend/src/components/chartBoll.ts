import type { Row } from "../lib/api";

export const BOLL_PERIOD = 20;
export const BOLL_STD_MULTIPLIER = 2;

export type BollPoint = {
  date: string;
  middle: number | null;
  upper: number | null;
  lower: number | null;
  warmupBars: number;
  reason: string;
};

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const validDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/**
 * Chart-only BOLL on returned closes in the chart's actual price basis.
 *
 * Server warm-up counts prove continuity, but cannot supply absent closes or
 * variance. Even a pre-warmed/truncated response needs 20 actual comparable
 * returned bars. No source data, strategy fields or server indicators change.
 */
export function chartBoll(data: Row | null | undefined): BollPoint[] {
  const bars: Row[] = Array.isArray(data?.bars) ? data.bars : [];
  const period = data?.period;
  const quoteMode = period === "D" && data?.indicator_continuity === "quotes";
  const basis = data?.price_basis;
  let chartReason = "";
  if (!["D", "W", "M"].includes(period)) chartReason = "BOLL的K线周期待核验";
  else if (!validDate(data?.as_of)) chartReason = "BOLL的观察日期待核验";
  else if (basis !== "none" && basis !== "forward_event_affine")
    chartReason = "BOLL的图表价格口径待核验";
  else if (
    basis === "forward_event_affine" &&
    (data?.adjustment?.usable !== true ||
      data?.adjustment?.anchor !== data?.as_of)
  )
    chartReason = "本地前复权依据或观察日锚点待核验，BOLL保持未知";

  const unit = period === "W" ? "完整周K" : period === "M" ? "完整月K" : "报价";
  let closes: number[] = [];
  let previousDate: string | null = null;
  let previousCount: number | null = null;
  let segmentReason = "";

  return bars.map((source) => {
    const bar: Row = source && typeof source === "object" ? source : {};
    const result: BollPoint = {
      date: typeof bar.date === "string" ? bar.date : "",
      middle: null,
      upper: null,
      lower: null,
      warmupBars: 0,
      reason: "",
    };
    const count = quoteMode
      ? bar.display_indicator_warmup_bars
      : (bar.display_indicator_warmup_bars ?? bar.indicator_warmup_bars);
    let reason = chartReason;
    if (!reason && (!validDate(bar.date) || bar.date > data!.as_of))
      reason = "K线日期无效或晚于观察日，BOLL保持未知";
    if (!reason && previousDate !== null && bar.date <= previousDate)
      reason = "K线日期重复或未按时间升序，BOLL重新预热";
    if (!reason && (!finite(bar.close) || bar.close <= 0))
      reason = "缺少有效收盘价，BOLL重新预热";
    if (!reason && bar.price_basis != null && bar.price_basis !== basis)
      reason = "K线与图表价格口径不一致，BOLL重新预热";
    if (
      !reason &&
      basis === "forward_event_affine" &&
      bar.adjustment_anchor != null &&
      bar.adjustment_anchor !== data?.as_of
    )
      reason = "K线复权锚点不一致，BOLL重新预热";
    if (
      !reason &&
      period !== "D" &&
      (bar.complete !== true ||
        bar.week_closed === false ||
        bar.month_closed === false ||
        bar.weekly_quote_coverage_complete === false ||
        bar.monthly_quote_coverage_complete === false ||
        bar.monthly_price_comparable === false ||
        bar.monthly_indicator_eligible === false ||
        bar.corporate_action === true)
    )
      reason = "形成中、报价不完整或价格不可比的周/月K，不纳入BOLL预热";
    if (!reason && (!finite(count) || !Number.isInteger(count) || count < 1))
      reason = "连续可比K线预热依据待核验，BOLL保持未知";

    if (reason) {
      closes = [];
      previousCount = null;
      if (
        validDate(bar.date) &&
        (previousDate === null || bar.date > previousDate)
      )
        previousDate = bar.date;
      segmentReason = reason;
      return { ...result, reason };
    }

    // Quote mode deliberately ignores strict market-day gap flags. Its own
    // display count still restarts at unadjusted corporate actions. Aggregate
    // periods and strict daily mode retain their server-declared boundaries.
    const declaredReset =
      bar.corporate_action === true ||
      (!quoteMode &&
        (bar.price_continuity === false ||
          !!bar.indicator_reset_reason ||
          !!bar.weekly_continuity_reset_reason ||
          !!bar.monthly_continuity_reset_reason));
    const counterReset = previousCount !== null && count !== previousCount + 1;
    if (declaredReset || counterReset) {
      closes = [];
      segmentReason =
        (quoteMode
          ? bar.display_indicator_reset_reason
          : (bar.display_indicator_reset_reason ??
            bar.indicator_reset_reason)) ||
        (counterReset
          ? "返回K线的连续预热计数中断，BOLL重新预热"
          : "价格连续性重置，BOLL重新预热");
    }
    previousDate = bar.date;
    previousCount = count;
    closes.push(bar.close);
    if (closes.length > BOLL_PERIOD) closes.shift();
    result.warmupBars = closes.length;
    if (closes.length < BOLL_PERIOD) {
      result.reason = `${segmentReason ? segmentReason + "；" : ""}BOLL需要${BOLL_PERIOD}根连续可比${unit}，当前已取得${closes.length}根`;
      return result;
    }

    // Center before summing to avoid subtracting two nearly equal squared
    // prices; population variance divides by N, not the sample's N - 1.
    const origin = closes[0];
    const offsets = closes.map((close) => close - origin);
    const meanOffset =
      offsets.reduce((sum, value) => sum + value, 0) / BOLL_PERIOD;
    const middle = origin + meanOffset;
    const variance =
      offsets.reduce((sum, value) => sum + (value - meanOffset) ** 2, 0) /
      BOLL_PERIOD;
    const width = BOLL_STD_MULTIPLIER * Math.sqrt(variance);
    const upper = middle + width;
    const lower = middle - width;
    if (![middle, upper, lower].every(finite))
      return { ...result, reason: "BOLL数值超出可计算范围，保持未知" };
    return { ...result, middle, upper, lower };
  });
}
