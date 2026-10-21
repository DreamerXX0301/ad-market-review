import type { Row } from "../lib/api";

/** Consume the shared indicator result; never infer a return from adjacent UI bars. */
export type ChartPeriod = "D" | "W" | "M";
export function candleChange(bar: Row | undefined, period: ChartPeriod) {
  const cycle = period === "M" ? "月" : "周";
  const label = period === "D" ? "当日涨跌幅" : cycle + "涨跌幅";
  const value = bar?.change_pct;
  const valid =
    typeof value === "number" &&
    Number.isFinite(value) &&
    bar?.price_continuity !== false &&
    bar?.corporate_action !== true;
  const comparison =
    period !== "D"
      ? `所选${cycle}收盘相对上一可比${cycle}收盘，按本图价格口径计算`
      : "当日收盘相对上一可比市场交易日收盘，按本图价格口径计算";
  return {
    label,
    value: valid ? value : null,
    reason: valid
      ? comparison +
        ((period === "W" && bar?.week_closed === false) ||
        (period === "M" && bar?.month_closed === false)
          ? `；本${cycle}形成中，显示截至本日的周期比较`
          : "")
      : bar?.indicator_reset_reason ||
        ((period === "W" && bar?.weekly_quote_coverage_complete === false) ||
        (period === "M" && bar?.monthly_quote_coverage_complete === false)
          ? `${cycle}内报价有缺口，不能核验本周期涨跌幅`
          : "缺少上一可比收盘价或连续性未核验，保持未知"),
  };
}
