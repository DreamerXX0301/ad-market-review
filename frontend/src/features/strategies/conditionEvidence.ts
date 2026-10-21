type CountCheck = {
  field?: unknown;
  actual?: unknown;
  actual_lower_bound?: unknown;
  actual_upper_bound?: unknown;
};

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** A verified lower bound must never be presented as the complete yearly count. */
export function yearlyLimitCountText(check: CountCheck): string | null {
  if (check.field !== "limit_up_count_1y") return null;
  if (count(check.actual)) return `实际 ${check.actual} 次`;
  if (
    count(check.actual_lower_bound) &&
    (check.actual_upper_bound == null ||
      (count(check.actual_upper_bound) &&
        check.actual_upper_bound >= check.actual_lower_bound))
  )
    return `已确认至少 ${check.actual_lower_bound} 次，完整次数待核验`;
  return "近一年次数待核验";
}
