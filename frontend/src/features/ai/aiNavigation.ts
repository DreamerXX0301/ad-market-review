import type { AIKind } from "./aiTypes";

const marketStages = {
  auction: "09:25",
  "0935": "09:35",
  "1000": "10:00",
  "1030": "10:30",
  "1100": "11:00",
  noon: "11:30",
  "1330": "13:30",
  "1400": "14:00",
  "1430": "14:30",
  close: "15:00",
} as const;

export type AIMarketStage = keyof typeof marketStages;

export function aiMarketStage(value: unknown): AIMarketStage | undefined {
  return typeof value === "string" && Object.hasOwn(marketStages, value)
    ? (value as AIMarketStage)
    : undefined;
}

export interface AILaunchContext {
  kind: AIKind;
  instrumentId?: string;
  reportId?: string;
  asOf?: string;
  marketStage?: AIMarketStage;
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

export function aiMarketQuestion({ kind, asOf, marketStage }: AILaunchContext) {
  const day = safeDay(asOf ?? null);
  if (kind !== "market" || !day || !marketStage) return "";
  const at = `${day} ${marketStages[marketStage]}`;
  if (marketStage === "auction")
    return `解读 ${at} 的早盘竞价。昨日高标反馈怎么样？重点名单之外，哪些行业或题材出现多股同步走强？请列出代表股和已核验的竞价涨幅、成交额。`;
  if (marketStage === "noon")
    return `解读 ${day} 的午评快照。请串联当天竞价、截至午评实际采集时点的全部已存上午盘中观察和午评本身，分析题材走强或走弱、成交变化及涨停梯队反馈。只使用该时点之前的数据，未采到的时段说明缺档。`;
  if (marketStage === "close")
    return `解读 ${at} 的收盘复盘。结合当天竞价、盘中和正式收盘，梳理题材轮动、成交和涨停梯队变化。`;
  return `解读 ${at} 的盘中观察快照。这个时点哪些方向走强、哪些走弱？结合该时点成交和涨停梯队，有同股报价时再与早盘竞价对照。`;
}

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
  if (
    context.kind === "market" &&
    context.marketStage &&
    safeDay(context.asOf ?? null)
  )
    params.set("ai_stage", context.marketStage);
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
  const marketStage =
    kind === "market" && asOf
      ? aiMarketStage(params.get("ai_stage"))
      : undefined;
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
    marketStage,
    explicit,
    launchId,
    key:
      launchId ||
      JSON.stringify([
        kind,
        instrumentId,
        reportId,
        asOf,
        sessionId,
        marketStage,
      ]),
  };
}
