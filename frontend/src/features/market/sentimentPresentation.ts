import type { Row } from "../../lib/api";

const returnKeys = new Set([
  "equal_weight_return_pct",
  "median_return_pct",
  "previous_limit_up_avg_return_pct",
  "previous_limit_up_median_return_pct",
  "index_daily_return_pct",
  "index_20d_return_pct",
  "index_ma20_distance_pct",
]);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

/** Only price-return metrics use financial red/green; counts and ratios do not. */
export function sentimentMetricView(metric: Row) {
  const unit = ["%", "家", "板"].includes(metric.unit) ? metric.unit : "";
  const isReturn = returnKeys.has(metric.key);
  const valid =
    finite(metric.value) &&
    (unit === "家" || unit === "板"
      ? Number.isInteger(metric.value) && metric.value >= 0
      : metric.key === "index_20d_volatility_pct"
        ? metric.value >= 0
        : unit === "%" && !isReturn
          ? metric.value >= 0 && metric.value <= 100
          : true);
  const reason = typeof metric.reason === "string" ? metric.reason.trim() : "";
  const formula =
    typeof metric.formula === "string" ? metric.formula.trim() : "";
  const valueText = valid
    ? (isReturn && metric.value > 0 ? "+" : "") +
      metric.value.toLocaleString("zh-CN", {
        minimumFractionDigits: unit === "%" ? 2 : 0,
        maximumFractionDigits: unit === "%" ? 2 : 0,
      })
    : "—";
  const fallback = valid ? "" : "该指标尚无可核验读数，不能按零处理。";
  return {
    known: valid,
    valueText,
    unit,
    tone:
      valid && isReturn && metric.value !== 0
        ? metric.value > 0
          ? "up"
          : "down"
        : "",
    help: [formula, reason || fallback].filter(Boolean).join("；"),
    reason,
  };
}

export function sentimentCount(value: unknown) {
  return finite(value) && Number.isInteger(value) && value >= 0
    ? value.toLocaleString("zh-CN")
    : "待核验";
}

export function sentimentCoverage(value: unknown) {
  return finite(value) && value >= 0 && value <= 100
    ? value.toLocaleString("zh-CN", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }) + "%"
    : "待核验";
}

export function sentimentReferences(value: unknown): Row[] {
  if (!Array.isArray(value)) return [];
  const used = new Set<string>();
  return value.filter((row) => {
    if (
      !row ||
      typeof row !== "object" ||
      typeof row.url !== "string" ||
      !/^https?:\/\//i.test(row.url) ||
      used.has(row.url)
    )
      return false;
    used.add(row.url);
    return true;
  });
}

export function sentimentPromotionBounds(value: unknown) {
  if (!value || typeof value !== "object") return null;
  const { lower, upper } = value as Row;
  if (
    !finite(lower) ||
    !finite(upper) ||
    lower < 0 ||
    upper > 100 ||
    lower > upper
  )
    return null;
  const fmt = (n: number) =>
    n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return lower === upper
    ? fmt(lower) + "%"
    : fmt(lower) + "%—" + fmt(upper) + "%";
}

export function sentimentKnownTime(value: unknown) {
  if (typeof value !== "string" || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(value))
    return "未登记";
  const time = new Date(value);
  return Number.isFinite(time.getTime())
    ? time.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      }) + " 北京时间"
    : "未登记";
}
