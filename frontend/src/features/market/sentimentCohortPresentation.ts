import type { Row } from "../../lib/api";

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const day = (value: unknown): value is string =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

/** Price feedback has its own dated scope; strict current-regime metrics stay separate. */
export function cohortPriceFeedback(group: Row, date: unknown): Row | null {
  const price = group.cohort_price_feedback;
  if (
    !price ||
    typeof price !== "object" ||
    Array.isArray(price) ||
    price.version !== "previous-group-price-feedback-v1" ||
    price.group_id !== group.id ||
    !day(date) ||
    price.date !== date ||
    !day(group.previous_date) ||
    price.cohort_date !== group.previous_date ||
    price.cohort_date >= date ||
    price.price_basis !== "none" ||
    !count(price.sample_count) ||
    !count(price.observed_count) ||
    price.observed_count !== group.observed_count ||
    !count(price.unknown_count) ||
    price.sample_count + price.unknown_count !== price.observed_count ||
    !["insufficient", "strong", "weak", "mixed"].includes(price.state) ||
    !Array.isArray(price.metrics) ||
    price.metrics.some(
      (metric: unknown) =>
        !metric || typeof metric !== "object" || Array.isArray(metric),
    )
  )
    return null;
  return price;
}
