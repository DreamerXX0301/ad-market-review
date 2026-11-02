import type { Row } from "../../lib/api";

export type AutomaticPlan = { enabled: boolean; time: string };
export type AutomaticAuctionPlan = {
  enabled: boolean;
  revision: number;
  mode: "off" | "daily" | "once";
  fixed_time: "09:25:30";
  window_end: "09:30:00";
  can_enable: boolean;
  legacy_configuration: boolean;
  legacy_instrument_count: number;
  reason?: string | null;
  next_run_at?: string | null;
  last_run?: Row | null;
  worker?: Row;
};
export type AutomaticReviewSettingsValue = {
  timezone: "Asia/Shanghai";
  revision: number;
  noon: AutomaticPlan;
  after_close: AutomaticPlan;
  auction: AutomaticAuctionPlan;
  now?: string;
  last_checks?: { noon?: Row | null; after_close?: Row | null };
  policy?: Row;
};

/** Translate internal process terms only for display; retain original settings. */
export function automaticStatusText(value: unknown, fallback = ""): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  return value
    .replace(/API\/worker/g, "服务")
    .replace(/(?:唯一|服务端)?worker/g, "采集服务")
    .replace(/工作进程/g, "采集服务");
}

export function automaticTimeError(
  kind: "noon" | "after_close",
  value: string,
): string {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value))
    return "时间需要填写为有效的24小时制 HH:mm。";
  if (kind === "noon" && (value < "11:31" || value >= "13:00"))
    return "午评时间需在北京时间11:31至13:00前。";
  if (kind === "after_close" && value < "17:00")
    return "盘后复盘时间需在北京时间17:00至23:59。";
  return "";
}

const revision = (value: unknown) =>
  Number.isSafeInteger(value) && (value as number) >= 0;
const record = (value: unknown): value is Row =>
  !!value && typeof value === "object" && !Array.isArray(value);

/** All three authorizations must be known before any combined settings write. */
export function automaticReviewSettings(
  value: unknown,
): AutomaticReviewSettingsValue | null {
  if (!record(value)) return null;
  if (value.timezone !== "Asia/Shanghai" || !revision(value.revision))
    return null;
  for (const kind of ["noon", "after_close"] as const) {
    const plan = value[kind];
    if (
      !record(plan) ||
      typeof plan.enabled !== "boolean" ||
      typeof plan.time !== "string" ||
      automaticTimeError(kind, plan.time)
    )
      return null;
  }
  const auction = value.auction;
  if (
    !record(auction) ||
    typeof auction.enabled !== "boolean" ||
    !revision(auction.revision) ||
    !["off", "daily", "once"].includes(auction.mode) ||
    auction.fixed_time !== "09:25:30" ||
    auction.window_end !== "09:30:00" ||
    typeof auction.can_enable !== "boolean" ||
    typeof auction.legacy_configuration !== "boolean" ||
    !revision(auction.legacy_instrument_count)
  )
    return null;
  return value as AutomaticReviewSettingsValue;
}

export function automaticSettingsChanged(
  a: AutomaticReviewSettingsValue,
  b: AutomaticReviewSettingsValue,
): boolean {
  return (
    a.auction.enabled !== b.auction.enabled ||
    (["noon", "after_close"] as const).some(
      (kind) =>
        a[kind].enabled !== b[kind].enabled || a[kind].time !== b[kind].time,
    )
  );
}

export function automaticSettingsStale(
  draft: AutomaticReviewSettingsValue,
  saved: AutomaticReviewSettingsValue,
): boolean {
  return (
    draft.revision !== saved.revision ||
    draft.auction.revision !== saved.auction.revision
  );
}

/** Send only editable fields; legacy mode/date/list and status are never writes. */
export function automaticSettingsRequest(value: AutomaticReviewSettingsValue) {
  return {
    expected_revision: value.revision,
    expected_auction_revision: value.auction.revision,
    noon: { enabled: value.noon.enabled, time: value.noon.time },
    after_close: {
      enabled: value.after_close.enabled,
      time: value.after_close.time,
    },
    auction: { enabled: value.auction.enabled },
  };
}

export function automaticSavedResponse(
  baseline: AutomaticReviewSettingsValue,
  submitted: AutomaticReviewSettingsValue,
  response: unknown,
): AutomaticReviewSettingsValue | null {
  const saved = automaticReviewSettings(response);
  if (
    !saved ||
    automaticSettingsChanged(saved, submitted) ||
    saved.revision < baseline.revision ||
    saved.auction.revision < baseline.auction.revision ||
    saved.auction.legacy_configuration ||
    saved.auction.mode !== (submitted.auction.enabled ? "daily" : "off")
  )
    return null;
  const reviewChanged = (["noon", "after_close"] as const).some(
    (kind) =>
      baseline[kind].enabled !== submitted[kind].enabled ||
      baseline[kind].time !== submitted[kind].time,
  );
  if (reviewChanged && saved.revision !== baseline.revision + 1) return null;
  if (
    baseline.auction.enabled !== submitted.auction.enabled &&
    saved.auction.revision !== baseline.auction.revision + 1
  )
    return null;
  return saved;
}
