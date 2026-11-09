import type { AIKind } from "./aiTypes";

export interface ChatPage {
  kind: AIKind;
  pageRoute?: "ai";
  pageLaunchId?: string;
  resolvedSession?: boolean;
  hasNewsDraft?: boolean;
  hasMarketScopeDraft?: boolean;
  instrumentId?: string;
  reportId?: string;
  asOf?: string;
}
const routeFor = (page: ChatPage) =>
  page.pageRoute || (page.kind === "stock" ? "research" : "market");
const safeInstrumentId = (value: unknown) =>
  typeof value === "string" && /^[0-9]{6}\.(SH|SZ|BJ)$/.test(value)
    ? value
    : undefined;
export function readChatLocation(hash: string, page: ChatPage) {
  const [route, search = ""] = hash.replace(/^#/, "").split("?", 2);
  if (route !== routeFor(page)) return "";
  const params = new URLSearchParams(search);
  if (
    page.pageRoute === "ai" &&
    params.get("ai_launch") &&
    params.get("ai_launch") !== page.pageLaunchId
  )
    return "";
  const id = params.get("ai_chat") || "";
  if (!/^[a-zA-Z0-9_-]{1,200}$/.test(id)) return "";
  if (page.pageRoute === "ai") return id;
  if (page.kind === "market" && page.asOf && params.get("ai_day") !== page.asOf)
    return "";
  if (page.instrumentId && page.instrumentId !== params.get("ai_stock"))
    return "";
  if (page.reportId && page.reportId !== params.get("ai_report")) return "";
  return id;
}
export function chatLocation(hash: string, page: ChatPage, id: string) {
  const [route, search = ""] = hash.replace(/^#/, "").split("?", 2);
  if (route !== routeFor(page)) return hash;
  const params = new URLSearchParams(search);
  if (
    page.pageRoute === "ai" &&
    params.get("ai_launch") &&
    params.get("ai_launch") !== page.pageLaunchId
  )
    return hash;
  if (page.pageRoute === "ai" && page.pageLaunchId)
    params.set("ai_launch", page.pageLaunchId);
  const same = params.get("ai_chat") === id && !page.resolvedSession;
  const saved = {
    asOf: same ? params.get("ai_day") : null,
    instrumentId: same ? params.get("ai_stock") : null,
    reportId: same ? params.get("ai_report") : null,
  };
  for (const key of ["ai_chat", "ai_day", "ai_stock", "ai_report"])
    params.delete(key);
  if (id || !page.hasNewsDraft)
    for (const key of ["ai_news", "ai_source", "ai_item", "ai_days"])
      params.delete(key);
  if (id || !page.hasMarketScopeDraft) params.delete("ai_scope");
  if (page.pageRoute === "ai") params.set("ai_kind", page.kind);
  if (id || page.kind !== "stock" || !safeInstrumentId(page.instrumentId))
    params.delete("ai_name");
  if (id || page.pageRoute === "ai") {
    if (id) {
      params.set("ai_chat", id);
      params.delete("ai_stage");
    }
    if (page.asOf || saved.asOf) params.set("ai_day", page.asOf || saved.asOf!);
    const instrumentId =
      safeInstrumentId(page.instrumentId) ||
      safeInstrumentId(saved.instrumentId);
    if (instrumentId) params.set("ai_stock", instrumentId);
    if (page.reportId || saved.reportId)
      params.set("ai_report", page.reportId || saved.reportId!);
  }
  return `#${route}${params.size ? `?${params}` : ""}`;
}
