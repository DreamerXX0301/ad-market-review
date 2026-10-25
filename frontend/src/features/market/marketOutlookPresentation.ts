import type { Row } from "../../lib/api";

export const OUTLOOK_VERSION = "dated-market-outlook-v1";
export const INSIGHTS_VERSION = "market-opportunity-insights-v1";
export const outlookRows = (value: unknown): Row[] =>
  Array.isArray(value)
    ? value.filter((item) => item && typeof item === "object")
    : [];
export const outlookLines = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim())
    : [];
export const outlookNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** An old response cannot supply the new date's plan or candidate list. */
export function outlookForDate(
  value: Row | null | undefined,
  date: string,
  snapshot: unknown,
) {
  return value?.version === OUTLOOK_VERSION &&
    value.date === date &&
    typeof snapshot === "string" &&
    snapshot &&
    value.snapshot_id === snapshot
    ? value
    : null;
}

/** Additive insights have their own date/generation boundary, including history. */
export function outlookInsightsForDate(
  value: Row | null | undefined,
  date: string,
  snapshot: unknown,
) {
  return value?.version === INSIGHTS_VERSION &&
    value.date === date &&
    typeof snapshot === "string" &&
    snapshot &&
    value.snapshot_id === snapshot
    ? value
    : null;
}

function outlookDay(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value
  );
}

export function outlookChanges(value: unknown, day: string): Row[] {
  const rows = outlookRows(value);
  return rows.filter(
    (row) =>
      typeof row.id === "string" &&
      rows.filter((item) => item.id === row.id).length === 1 &&
      row.date === day &&
      (row.previous_date == null ||
        (outlookDay(row.previous_date) && row.previous_date < day)),
  );
}

export function outlookRoutes(value: unknown): Row[] {
  const rows = outlookRows(value);
  return ["trend", "relay", "repair"].flatMap((id) => {
    const matches = rows.filter((row) => row.id === id);
    return matches.length === 1 ? matches : [];
  });
}

/** Display only the server's bounded replay through the selected observation day. */
export function outlookCandidateTracking(
  value: Row | null | undefined,
  day: string,
) {
  if (
    !value ||
    value.as_of !== day ||
    value.lookback_sessions !== 20 ||
    value.confirmed !== false ||
    ![
      "candidate",
      "continuing",
      "diverging",
      "failed",
      "unknown",
      "none",
    ].includes(value.status) ||
    (value.candidate_date != null &&
      (!outlookDay(value.candidate_date) || value.candidate_date > day)) ||
    (value.last_observed_date != null &&
      (!outlookDay(value.last_observed_date) ||
        value.last_observed_date > day)) ||
    (value.window_begin != null &&
      (!outlookDay(value.window_begin) || value.window_begin > day)) ||
    (value.window_end != null &&
      (!outlookDay(value.window_end) || value.window_end > day)) ||
    (value.window_begin &&
      value.window_end &&
      value.window_begin > value.window_end)
  )
    return null;
  const observations = outlookRows(value.observations);
  return {
    ...value,
    observations: observations
      .filter(
        (row) =>
          outlookDay(row.date) &&
          row.date <= day &&
          (!value.candidate_date || row.date >= value.candidate_date) &&
          (!value.window_begin || row.date >= value.window_begin) &&
          (!value.window_end || row.date <= value.window_end) &&
          observations.filter((item) => item.date === row.date).length === 1,
      )
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(-20),
    index_references: outlookRows(value.index_references).filter(
      (row) =>
        value.candidate_date &&
        row.date === value.candidate_date &&
        row.date <= day,
    ),
  };
}

export function outlookStocks(value: unknown, day: string): Row[] {
  const used = new Set<string>();
  return outlookRows(value).filter((row) => {
    const id = row.instrument_id;
    if (
      typeof id !== "string" ||
      !/^\d{6}\.(SH|SZ|BJ)$/.test(id) ||
      row.evidence_date !== day ||
      used.has(id)
    )
      return false;
    used.add(id);
    return true;
  });
}

export function outlookAction(value: Row) {
  const valid =
    ["blocked", "avoid", "wait", "conditional"].includes(value.status) &&
    value.trigger_state === "unobserved";
  return {
    status: valid ? value.status : "unknown",
    label: valid ? value.status_label || "等待确认" : "待核验",
  };
}

/** Compact labels from server evidence; no new score or stage calculation. */
export function outlookAxisEvidence(axis: Row): string {
  if (typeof axis.evidence === "string") return axis.evidence;
  const rows = outlookRows(axis.evidence);
  if (axis.id === "index")
    return rows
      .map(
        (row) =>
          `${row.name || row.instrument_id} ${row.state_label || "待核验"}`,
      )
      .join(" · ");
  const chosen =
    axis.id === "short_term"
      ? rows.filter(
          (row) =>
            String(row.label || "").includes("中位") ||
            String(row.label || "").includes("普通10%制度组证据"),
        )
      : rows;
  return chosen
    .map((row) => {
      const value = outlookNumber(row.value);
      const text =
        value == null
          ? typeof row.value === "string"
            ? row.value
            : "待核验"
          : value.toLocaleString("zh-CN", { maximumFractionDigits: 1 }) +
            (row.unit || "");
      return `${row.label || "指标"} ${text}`;
    })
    .join(" · ");
}
