import type { Row } from "../../lib/api";

export const OUTLOOK_VERSION = "dated-market-outlook-v1";
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
