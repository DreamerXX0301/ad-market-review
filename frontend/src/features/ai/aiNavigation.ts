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
  instrumentName?: string;
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
const safeInstrumentId = (value: unknown) =>
  typeof value === "string" && /^[0-9]{6}\.(SH|SZ|BJ)$/.test(value)
    ? value
    : undefined;
const safeInstrumentName = (value: unknown) => {
  if (typeof value !== "string") return undefined;
  const name = value.trim();
  return /^[\p{L}\p{N}*＊·.&＆()（） -]{1,60}$/u.test(name) ? name : undefined;
};
const safeDay = (value: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.valueOf()) &&
    date.toISOString().slice(0, 10) === value
    ? value
    : undefined;
};

export function aiLaunchQuestion({
  kind,
  asOf,
  marketStage,
  instrumentId,
  instrumentName,
  reportId,
}: AILaunchContext) {
  const day = safeDay(asOf ?? null);
  if (kind === "stock") {
    const code = safeInstrumentId(instrumentId);
    if (!code) return "";
    const name = safeInstrumentName(instrumentName);
    const stock = name && name !== code ? `${name}（${code}）` : code;
    const basis = safeId(reportId ?? null)
      ? `结合这份${day ? `行情截至 ${day} 的` : ""}个股研究报告和可核验资料`
      : day
        ? `以 ${day} 为观察日`
        : "结合最新可核验资料";
    return `请研究${stock}。${basis}，分析主营业务、行业景气度、营收利润、现金流、估值和主要风险，标明关键数据的日期和来源。`;
  }
  if (!day) return "";
  const stage = aiMarketStage(marketStage);
  if (!stage)
    return `请复盘 ${day} 的 A 股盘面。梳理指数、成交额、市场情绪、行业与题材轮动和涨停梯队，说明当天的主要变化。若收盘数据还未发布，请分析截至最新已留存的时点。`;
  const at = `${day} ${marketStages[stage]}`;
  if (stage === "auction")
    return `解读 ${at} 的早盘竞价。昨日高标反馈怎么样？重点名单之外，哪些行业或题材出现多股同步走强？请列出代表股和已核验的竞价涨幅、成交额。`;
  if (stage === "noon")
    return `解读 ${day} 的午评快照。请串联当天竞价、截至午评实际采集时点的全部已存上午盘中观察和午评本身，分析题材走强或走弱、成交变化及涨停梯队反馈。只使用该时点之前的数据，未采到的时段说明缺档。`;
  if (stage === "close")
    return `解读 ${at} 的收盘复盘。结合当天竞价、盘中和正式收盘，梳理题材轮动、成交和涨停梯队变化。`;
  return `解读 ${at} 的盘中观察快照。串联当天竞价到该时点的留存数据，说明哪些方向走强或走弱、成交和涨停梯队如何变化。有同股报价时对照竞价，缺档请说明。`;
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
  for (const [key, value] of Object.entries({
    ai_chat: safeId(original.get("ai_chat")),
    ai_stock: safeInstrumentId(original.get("ai_stock")),
    ai_report: safeId(original.get("ai_report")),
    ai_day: safeDay(original.get("ai_day")),
  }))
    if (value) params.set(key, value);
  return `#ai?${params}`;
}

export function aiLaunchLocation(context: AILaunchContext, launchId?: string) {
  const params = new URLSearchParams({ ai_kind: context.kind });
  const instrumentId = safeInstrumentId(context.instrumentId);
  const reportId = safeId(context.reportId ?? null);
  const day = safeDay(context.asOf ?? null);
  if (instrumentId) params.set("ai_stock", instrumentId);
  const name =
    context.kind === "stock" && instrumentId
      ? safeInstrumentName(context.instrumentName)
      : undefined;
  if (name && name !== instrumentId) params.set("ai_name", name);
  if (reportId) params.set("ai_report", reportId);
  if (day) params.set("ai_day", day);
  const stage =
    context.kind === "market" && day
      ? aiMarketStage(context.marketStage)
      : undefined;
  if (stage) params.set("ai_stage", stage);
  if (launchId) params.set("ai_launch", launchId);
  return `#ai?${params}`;
}

/** Bare sidebar navigation preserves the mounted chat; explicit links open it. */
export function readAILaunch(hash: string): AILaunch {
  const [route, search = ""] = upgradeChatLocation(hash)
    .replace(/^#/, "")
    .split("?", 2);
  const params = new URLSearchParams(route === "ai" ? search : "");
  const instrumentId = safeInstrumentId(params.get("ai_stock"));
  const reportId = safeId(params.get("ai_report"));
  const asOf = safeDay(params.get("ai_day"));
  const sessionId = safeId(params.get("ai_chat"));
  const launchId = safeId(params.get("ai_launch"));
  const kind: AIKind =
    params.get("ai_kind") === "stock" || instrumentId ? "stock" : "market";
  const instrumentName =
    kind === "stock" && instrumentId && !sessionId
      ? safeInstrumentName(params.get("ai_name"))
      : undefined;
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
    instrumentName,
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
        instrumentName,
        reportId,
        asOf,
        sessionId,
        marketStage,
      ]),
  };
}
