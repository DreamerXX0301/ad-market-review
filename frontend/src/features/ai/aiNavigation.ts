import type { AIKind } from "./aiTypes";

export interface AILaunchContext {
  kind: AIKind;
  instrumentId?: string;
  reportId?: string;
  asOf?: string;
}
export interface AILaunch extends AILaunchContext {
  launchId?: string;
  key: string;
  explicit: boolean;
}
const safeId = (value: string | null) =>
  value && /^[a-zA-Z0-9_-]{1,200}$/.test(value) ? value : undefined;
const safeDay = (value: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.valueOf()) &&
    date.toISOString().slice(0, 10) === value
    ? value
    : undefined;
};

/** Old shared links keep their conversation when the chat moves to its own page. */
export function upgradeChatLocation(hash: string) {
  const [route, search = ""] = hash.replace(/^#/, "").split("?", 2);
  const original = new URLSearchParams(search);
  if (
    !["market", "research"].includes(route) ||
    !safeId(original.get("ai_chat"))
  )
    return hash;
  const params = new URLSearchParams();
  params.set("ai_kind", route === "research" ? "stock" : "market");
  for (const key of ["ai_chat", "ai_stock", "ai_report", "ai_day"])
    if (original.get(key)) params.set(key, original.get(key)!);
  return `#ai?${params}`;
}

export function aiLaunchLocation(context: AILaunchContext, launchId?: string) {
  const params = new URLSearchParams({ ai_kind: context.kind });
  if (context.instrumentId) params.set("ai_stock", context.instrumentId);
  if (context.reportId) params.set("ai_report", context.reportId);
  if (context.asOf) params.set("ai_day", context.asOf);
  if (launchId) params.set("ai_launch", launchId);
  return `#ai?${params}`;
}

/** Bare sidebar navigation preserves the mounted chat; explicit links open it. */
export function readAILaunch(hash: string): AILaunch {
  const [route, search = ""] = upgradeChatLocation(hash)
    .replace(/^#/, "")
    .split("?", 2);
  const params = new URLSearchParams(route === "ai" ? search : "");
  const instrument = params.get("ai_stock");
  const instrumentId =
    instrument && /^\d{6}\.(SH|SZ|BJ)$/.test(instrument)
      ? instrument
      : undefined;
  const reportId = safeId(params.get("ai_report"));
  const asOf = safeDay(params.get("ai_day"));
  const sessionId = safeId(params.get("ai_chat"));
  const launchId = safeId(params.get("ai_launch"));
  const kind: AIKind =
    params.get("ai_kind") === "stock" || instrumentId ? "stock" : "market";
  const explicit =
    route === "ai" &&
    !!(
      params.has("ai_kind") ||
      instrumentId ||
      reportId ||
      asOf ||
      sessionId ||
      launchId
    );
  return {
    kind,
    instrumentId,
    reportId,
    asOf,
    explicit,
    launchId,
    key:
      launchId ||
      JSON.stringify([kind, instrumentId, reportId, asOf, sessionId]),
  };
}
