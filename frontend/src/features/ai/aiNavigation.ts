import type { AIKind, AIMarketScope, AINewsContext } from "./aiTypes";

const marketStages = {
  auction: "09:25",
  "0935": "09:35",
  "0945": "09:45",
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
  marketScope?: AIMarketScope;
  newsContext?: AINewsContext;
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

const archiveKey = (value: string | null, limit: number) => {
  if (!value || /[\x00-\x1f\x7f]/.test(value)) return undefined;
  const key = value.trim();
  return key && key.length <= limit ? key : undefined;
};

function readNewsContext(params: URLSearchParams): AINewsContext | undefined {
  const kind = params.get("ai_news");
  if (kind === "day") return { kind };
  const source = archiveKey(params.get("ai_source"), 120);
  const id = archiveKey(params.get("ai_item"), 200);
  if (!source || !id) return undefined;
  if (kind === "news") return { kind, source, id };
  const days = params.get("ai_days");
  if (kind === "event" && (days === "7" || days === "30"))
    return { kind, source, id, days: days === "7" ? 7 : 30 };
  return undefined;
}

export function aiLaunchQuestion({
  kind,
  asOf,
  marketStage,
  marketScope,
  newsContext,
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
  if (newsContext?.kind === "day")
    return `请梳理 ${day} 的已收录消息。按主题归纳值得关注的变化，标明发布时间和来源，说明有哪些时段覆盖不完整。`;
  if (newsContext?.kind === "news")
    return `请解读我在 ${day} 选中的这条消息。概括已知事实和背景，标明发布时间和来源，区分已证实的影响与推测。`;
  if (newsContext?.kind === "event")
    return `请解读我在 ${day} 选中的这项日历事件。说明事件时间、确认状态、值得关注的细节和来源，区分已知日程与尚未公布的结果。`;
  const stage = aiMarketStage(marketStage);
  if (!stage && marketScope === "full_review")
    return `请完整复盘 ${day} 的 A 股盘面。先概括当日结构与关键转折，再分节分析盘面总览（指数行情和成交额）、盘前竞价、盘中观察、午评快照、消息与事件、市场情绪、行业与概念、涨停梯队、龙虎榜、30 日涨幅榜、异动与监管。重点核对涨跌家数和昨日涨停反馈、涨停梯队各板高度和断层、炸板数量与集中方向、板块强弱变化和代表股。每节说明已核验的数据与含义，串联盘前、盘中到收盘的变化，标明数据日期、留存时点和来源。收盘尚未发布时，以最新已留存时点为准，逐项说明缺档或覆盖不足。`;
  if (!stage)
    return `请复盘 ${day} 的 A 股盘面。梳理指数、成交额、市场情绪、行业与题材轮动和涨停梯队，说明当天的主要变化。若收盘数据还未发布，请分析截至最新已留存的时点。`;
  const at = `${day} ${marketStages[stage]}`;
  if (stage === "auction")
    return `解读 ${at} 的早盘竞价。昨日高标反馈怎么样？重点名单之外，哪些行业或题材出现多股同步走强？请用本地数据列出代表股和已核验的竞价涨幅、成交额，缺档请说明。`;
  if (stage === "0945")
    return `解读 ${at} 的早盘跟踪快照。串联当天竞价、09:35 开盘观察和本档，说明哪些方向延续、转强或回落，结合成交变化、代表股和涨停梯队核对。只用截至本档的本地数据，缺档请说明。`;
  if (stage === "noon")
    return `解读 ${day} 的午评快照。请串联当天竞价、截至午评实际采集时点的全部已存上午盘中观察和午评本身，分析题材走强或走弱、成交变化及涨停梯队反馈。只用截至该时点的本地数据，未采到的时段说明缺档。`;
  if (stage === "close")
    return `解读 ${at} 的收盘复盘。结合当天竞价、盘中和正式收盘，梳理题材轮动、成交和涨停梯队变化。只用本地数据，缺档请说明。`;
  return `解读 ${at} 的盘中观察快照。串联当天竞价到该时点的本地数据，说明哪些方向走强或走弱、成交和涨停梯队如何变化。有同股报价时对照竞价，缺档请说明。`;
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
  const news = context.kind === "market" && day ? context.newsContext : null;
  if (news) {
    params.set("ai_news", news.kind);
    if (news.kind !== "day") {
      params.set("ai_source", news.source);
      params.set("ai_item", news.id);
      if (news.kind === "event") params.set("ai_days", String(news.days));
    }
  } else if (stage) {
    params.set("ai_stage", stage);
  } else if (
    context.kind === "market" &&
    day &&
    context.marketScope === "full_review"
  ) {
    params.set("ai_scope", context.marketScope);
  }
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
  const newsContext =
    kind === "market" && asOf && !sessionId
      ? readNewsContext(params)
      : undefined;
  const marketStage =
    kind === "market" && asOf && !newsContext
      ? aiMarketStage(params.get("ai_stage"))
      : undefined;
  const marketScope =
    kind === "market" &&
    asOf &&
    !sessionId &&
    !marketStage &&
    !newsContext &&
    params.get("ai_scope") === "full_review"
      ? "full_review"
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
    marketScope,
    newsContext,
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
        marketScope,
        newsContext,
      ]),
  };
}
