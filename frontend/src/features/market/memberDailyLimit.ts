import type { Row } from "../../lib/api";

const isDay = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/** The member API's latest-event proof is sufficient only when that event is today.
 * Never derive a price-limit event from a percentage or an annual count.
 */
export function memberHasConfirmedDailyLimit(row: Row, date: string): boolean {
  if (
    !isDay(date) ||
    row.quote_available !== true ||
    row.quote_date !== date ||
    typeof row.close !== "number" ||
    !Number.isFinite(row.close) ||
    row.close <= 0 ||
    row.last_limit_up_date !== date ||
    row.last_limit_up_complete !== true ||
    row.sessions_since_limit_up !== 0
  )
    return false;
  const proof = row.last_limit_up_coverage;
  return (
    proof != null &&
    typeof proof === "object" &&
    !Array.isArray(proof) &&
    proof.complete === true &&
    proof.state === "confirmed_latest" &&
    proof.since === date &&
    proof.until === date &&
    proof.covered_days === 1 &&
    proof.expected_days === 1 &&
    proof.unknown_days === 0
  );
}
