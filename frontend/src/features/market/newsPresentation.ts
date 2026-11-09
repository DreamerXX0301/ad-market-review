import { readNewsMove } from "./newsMove.ts";

type NewsClassificationInput = {
  title: string;
  topics?: readonly { name: string }[];
  symbols?: readonly { symbol: string }[];
  event_id?: string | null;
};

export const newsFilters = [
  { key: "policy", label: "政策宏观" },
  { key: "futures", label: "期货与商品" },
  { key: "overseas", label: "外围市场" },
  { key: "stocks", label: "个股与公告" },
  { key: "industry", label: "行业题材" },
  { key: "market", label: "市场动态" },
  { key: "other", label: "其他快讯" },
] as const;
export type NewsFilterKey = (typeof newsFilters)[number]["key"];

const categories = {
  macro: { key: "macro", label: "政策宏观", tone: "blue" },
  disclosure: { key: "disclosure", label: "公司公告", tone: "violet" },
  stocks: { key: "stocks", label: "个股动态", tone: "violet" },
  futures: { key: "futures", label: "期货与商品", tone: "amber" },
  overseas: { key: "overseas", label: "外围市场", tone: "blue" },
  industry: { key: "industry", label: "行业题材", tone: "teal" },
  market: { key: "market", label: "市场动态", tone: "amber" },
  other: { key: "other", label: "其他快讯", tone: "neutral" },
} as const;

const policyTopics = new Set([
  "经济数据及解读",
  "中国央行动态",
  "全球央行动态",
]);
const futuresTopics = new Set([
  "期货市场情报",
  "原油市场动态",
  "海外大宗商品",
  "能源类期货",
  "外汇期货",
  "股指期货",
  "禽畜期货",
]);
const overseasTopics = new Set([
  "环球市场情报",
  "美股动态",
  "港股动态",
  "中东冲突",
  "俄乌冲突快报",
]);
const marketTopics = new Set([
  "盘面直播",
  "期货市场情报",
  "原油市场动态",
  "美股动态",
  "港股动态",
  "人民币大新闻",
  "股指期货",
  "外汇期货",
  "ETF最新动态",
  "ETF大头条",
  "A股资金动向",
  "北交所最新动态",
  "融资融券",
  "限售股解禁",
  "公募基金动态",
]);
const industryTopics = new Set([
  "人工智能",
  "算力",
  "算力租赁",
  "数据中心",
  "服务器",
  "物理AI",
  "人形机器人",
  "PCB",
  "半导体芯片",
  "存储芯片",
  "英伟达概念",
  "TMT行业观察",
  "电力",
  "智能电网",
  "煤炭",
  "有色金属",
  "创新药",
  "医药",
  "猪肉",
  "船舶",
  "短剧",
  "冰雪产业",
  "玻璃",
  "建材",
  "橡胶",
  "轮胎",
  "天然气",
]);

const macroHeadline =
  /^(?:国务院(?:关税税则委员会|办公厅)?|国家统计局|中国人民银行|央行|美联储|财政部|国家发展改革委|证监会|商务部)[：:]|^(?:美联储|欧洲央行|日本央行)(?:宣布|将|维持|下调|上调|公布|召开|主席|降息|加息)|^央行今日开展/;
const policyHeadline =
  /^(?:(?:中国|美国|日本|韩国|英国|德国|法国|印度|加拿大|澳大利亚|欧盟)(?:政府|国会|议会|财政部|监管机构|总统|首相)?|美财政部|日本财务省)(?:拟|将|决定|宣布|出台|发布|实施|通过|批准|要求|取消|下调|上调).{0,32}(?:政策|法案|规定|措施|条例|税|补贴|限制|制裁|边境口岸|利率)/;
const disclosureHeadline =
  /^(?:【公司公告】|【公告】)|^[\p{L}\p{N}*·（）() -]{2,32}(?:发布|披露|公告)[：:]?(?:关于|定期报告|业绩预告|业绩快报|半年报|中报|年报|一季报|三季报)|^[\p{L}\p{N}*·（）() -]{2,32}公告[：:]/u;
const industryHeadline =
  /(?:板块|概念(?:股)?)(?:异动|盘初|盘中|午后|尾盘|延续|持续|集体|反复|震荡|大面积|表现|走强|走弱|回落|拉升|上涨|下跌|活跃|反弹|高开|低开)/;
const marketHeadline =
  /(?:指数|期货|收益率|资金净流入|资金净流出|两市融资余额|股价(?:上涨|下跌)|涨幅扩大|跌幅扩大|涨停|跌停|竞价|外汇储备)/;
const futuresHeadline =
  /(?:期货|主力合约|现货(?:黄金|白银|铜)|纽约期(?:金|银)|(?:布伦特|WTI)原油)/i;
const overseasHeadline =
  /(?:美股|港股|港交所|台交所|恒生(?:科技)?指数|日经225|日本东证|韩国(?:股市|KOSPI)|MSCI亚太|纳(?:斯达克|指)|标普500|道琼斯|美联储|欧洲央行|日本央行|美国|韩国|日本|英国|德国|法国|澳大利亚|欧盟|台湾股市|台积电|英伟达|特斯拉|SK海力士)/i;
const stockHeadline =
  /(?:^|[\s，、；：])(?:(?:台积电|英伟达|特斯拉|苹果|微软|亚马逊|谷歌|比亚迪|贵州茅台|宁德时代|SK海力士)|(?!(?:上市|多家|头部|龙头|个别|相关|多只|该|这家|企业|市场|两市))[\p{L}\p{N}*·（）()]{1,16}(?:股份|科技|电子|医药|药业|制药|集团|控股|能源|证券|保险|银行|汽车|新材|电气|电力|软件|通信|石化|矿业|公司))(?:股价|美股|港股|盘中|夜盘|大涨|大跌|涨|跌|一字|20cm|30cm|触及|发布|公告|披露|签署|达成|收购|与|[：:])/iu;
const routineBroadcast =
  /^(?:上一交易日股票型ETF份额变动前三|上一交易日ETF资金整体净申购|ETF两市融资融券余额)/;
const columnTeaser =
  /^【(?:盘中宝|玩转ETF|九点特供|点金互动易|研选[•·]研报数据)】/;
const teaserWording = /(?:这家(?:公司|企业)|该(?:行业|领域)|分析师强call)/i;
const materialIndustryHeadline =
  /(?:签署|签订|达成).{0,36}(?:合作|协议|订单)|(?:产品|项目|产线|工厂|试验|临床|药品).{0,12}(?:获批|投产|交付|注册|取得.{0,10}应答率)|(?:宣布|完成).{0,16}(?:并购|收购|回购)|集中交付/;
const spaceLaunchHeadline = /^(?:我国|中国).{0,8}成功发射.{0,28}卫星/;
const keyMarketAgenda = /^财联社\d{1,2}月\d{1,2}日早间新闻精选$/;
const materialSupplyHeadline =
  /(?:原油|石油|天然气|油轮|气田|油田|霍尔木兹海峡).{0,26}(?:出口|中断|关闭|停产|遭.{0,8}袭击|触雷|起火)/;

// Only collapse a single unqualified price update for the same named index.
// Multi-index updates, news with context and entries with other topics are kept.
const indexQuote =
  /^(台交所加权股价指数|韩国KOSPI指数|日经225指数|日本东证指数|MSCI亚太指数)(?:(涨幅扩大(?:至|，现涨)|(?:日内|最新)?(?:上涨|涨))|跌幅扩大(?:至|，现跌)|(?:日内|最新)?(?:下跌|跌))\d+(?:\.\d+)?%(?:[，,]?(?:最新报|至|报)[\d,.]+点)?[。.]*$/i;

export function classifyNews(message: NewsClassificationInput) {
  const originalTitle = message.title.trim();
  const title = originalTitle.replace(
    /^财联社\d{1,2}月\d{1,2}日电[，,：:]\s*/,
    "",
  );
  const topics = (message.topics ?? []).map(({ name }) => name.trim());
  const onlyMarketNotes = topics.every(
    (name) => name === "盘面直播" || name === "环球市场情报",
  );
  const regularQuote =
    !message.event_id && onlyMarketNotes ? indexQuote.exec(title) : null;
  const officialMacro = macroHeadline.test(title) || policyHeadline.test(title);
  const companyDisclosure = disclosureHeadline.test(title);
  const sectorMove = industryHeadline.test(title);
  const industryTopic = topics.some((name) => industryTopics.has(name));
  const move = readNewsMove(originalTitle);
  const policy = officialMacro || topics.some((name) => policyTopics.has(name));
  const futures =
    futuresHeadline.test(title) ||
    topics.some((name) => futuresTopics.has(name));
  const overseas =
    overseasHeadline.test(title) ||
    topics.some((name) => overseasTopics.has(name));
  const stocks =
    companyDisclosure ||
    Boolean(message.symbols?.length) ||
    stockHeadline.test(title);
  const category = policy
    ? categories.macro
    : companyDisclosure
      ? categories.disclosure
      : sectorMove
        ? categories.industry
        : futures
          ? categories.futures
          : stocks
            ? categories.stocks
            : industryTopic && materialIndustryHeadline.test(title)
              ? categories.industry
              : overseas
                ? categories.overseas
                : move ||
                    marketHeadline.test(title) ||
                    topics.some((name) => marketTopics.has(name))
                  ? categories.market
                  : industryTopic
                    ? categories.industry
                    : categories.other;
  const filters: NewsFilterKey[] = [];
  if (policy) filters.push("policy");
  if (futures) filters.push("futures");
  if (overseas) filters.push("overseas");
  if (stocks) filters.push("stocks");
  if (sectorMove || industryTopic) filters.push("industry");
  if (category.key === "market") filters.push("market");
  if (!filters.length) filters.push("other");

  const routine =
    (topics.length > 0 &&
      topics.every((name) => name === "盘面直播") &&
      routineBroadcast.test(title)) ||
    (topics.length === 0 &&
      columnTeaser.test(originalTitle) &&
      teaserWording.test(originalTitle));
  const mustShow = Boolean(message.event_id);
  const priority = routine
    ? 0
    : officialMacro || companyDisclosure
      ? 3
      : move ||
          (industryTopic && materialIndustryHeadline.test(title)) ||
          spaceLaunchHeadline.test(title)
        ? 2
        : sectorMove ||
            keyMarketAgenda.test(title) ||
            (overseas && materialSupplyHeadline.test(title))
          ? 1
          : 0;

  // Keep the latest selected quote per direction so a reversal stays visible.
  const quoteKey = regularQuote
    ? `${regularQuote[1].toUpperCase()}:${regularQuote[2] ? "up" : "down"}`
    : undefined;
  return { ...category, filters, move, mustShow, priority, quoteKey };
}

const FEATURED_LIMIT = 30;

export function selectFeaturedNews<
  T extends { category: ReturnType<typeof classifyNews> },
>(timeline: readonly T[]): T[] {
  const selected = new Set<T>();
  const tiers: T[][] = [[], [], [], []];
  const seenQuotes = new Set<string>();
  for (const entry of timeline) {
    const { mustShow, priority, quoteKey } = entry.category;
    if (mustShow) {
      selected.add(entry);
    } else if (priority && (!quoteKey || !seenQuotes.has(quoteKey))) {
      if (quoteKey) seenQuotes.add(quoteKey);
      tiers[priority].push(entry);
    }
  }
  for (const tier of [tiers[3], tiers[2], tiers[1]]) {
    for (const entry of tier) {
      if (selected.size >= FEATURED_LIMIT) break;
      selected.add(entry);
    }
  }
  return timeline.filter((entry) => selected.has(entry));
}
