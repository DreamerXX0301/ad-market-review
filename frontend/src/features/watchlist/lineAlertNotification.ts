/** Open saved current-business-day records; only a visual reminder. */
export const LINE_ALERTS_OPEN = "workbench-open-today-line-alerts";

// A retained same-path response must not announce yesterday's count as today's.
// The server may already know a newer business day than the shell bootstrap.
export function currentLineAlertSummary<
  T extends {
    business_date?: unknown;
    total?: unknown;
  },
>(summary: T | null, expectedDate?: string): T | null {
  if (
    !summary ||
    typeof summary.business_date !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(summary.business_date) ||
    (expectedDate && summary.business_date < expectedDate) ||
    typeof summary.total !== "number" ||
    !Number.isSafeInteger(summary.total) ||
    summary.total < 0
  )
    return null;
  return summary;
}
