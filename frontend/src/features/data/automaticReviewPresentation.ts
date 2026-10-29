import type { Row } from "../../lib/api";

export type AutomaticPlan = { enabled: boolean; time: string };
export type AutomaticReviewSettingsValue = {
  timezone: "Asia/Shanghai";
  revision: number;
  noon: AutomaticPlan;
  after_close: AutomaticPlan;
  now?: string;
  last_checks?: { noon?: Row | null; after_close?: Row | null };
  policy?: Row;
};

export function automaticTimeError(
  kind: "noon" | "after_close",
  value: string,
): string {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value))
    return "时间需要填写为有效的24小时制 HH:mm。";
  if (kind === "noon" && (value < "11:31" || value >= "13:00"))
    return "午评时间需在北京时间11:31至13:00前。";
  if (kind === "after_close" && value < "18:00")
    return "盘后复盘时间需在北京时间18:00至23:59。";
  return "";
}

/** Unknown or incomplete settings are never displayed as a saved disabled plan. */
export function automaticReviewSettings(
  value: unknown,
): AutomaticReviewSettingsValue | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Row;
  if (
    row.timezone !== "Asia/Shanghai" ||
    !Number.isSafeInteger(row.revision) ||
    row.revision < 0
  )
    return null;
  for (const kind of ["noon", "after_close"] as const) {
    const plan = row[kind];
    if (
      !plan ||
      typeof plan.enabled !== "boolean" ||
      typeof plan.time !== "string" ||
      automaticTimeError(kind, plan.time)
    )
      return null;
  }
  return row as AutomaticReviewSettingsValue;
}

export function automaticSettingsChanged(
  a: AutomaticReviewSettingsValue,
  b: AutomaticReviewSettingsValue,
): boolean {
  return (["noon", "after_close"] as const).some(
    (kind) =>
      a[kind].enabled !== b[kind].enabled || a[kind].time !== b[kind].time,
  );
}
