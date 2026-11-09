type NewsClassificationInput = {
  title: string;
  topics?: readonly { name: string }[];
  event_id?: string | null;
};

const categories = {
  macro: { key: "macro", label: "政策宏观", tone: "blue" },
  disclosure: { key: "disclosure", label: "公司公告", tone: "violet" },
  industry: { key: "industry", label: "行业题材", tone: "teal" },
  market: { key: "market", label: "市场动态", tone: "amber" },
  other: { key: "other", label: "其他快讯", tone: "neutral" },
} as const;

const macroTopics = new Set([
  "经济数据及解读",
  "中国央行动态",
  "中东冲突",
  "俄乌冲突快报",
  "全球央行动态",
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
const disclosureHeadline =
  /^(?:【公司公告】|【公告】)|^[\p{L}\p{N}*·（）() -]{2,32}(?:发布|披露|公告)[：:]?(?:关于|定期报告|业绩预告|业绩快报|半年报|中报|年报|一季报|三季报)|^[\p{L}\p{N}*·（）() -]{2,32}公告[：:]/u;
const industryHeadline =
  /(?:板块|概念)(?:异动|盘初|延续|反复|震荡|大面积|表现|走强|回落|拉升|上涨|下跌|活跃|反弹|高开|低开)/;
const marketHeadline =
  /(?:指数|期货|收益率|资金净流入|资金净流出|两市融资余额|股价(?:上涨|下跌)|涨幅扩大|跌幅扩大|涨停|跌停|竞价|外汇储备)/;
const routineBroadcast =
  /^(?:上一交易日股票型ETF份额变动前三|上一交易日ETF资金整体净申购|ETF两市融资融券余额)/;
const columnTeaser =
  /^【(?:盘中宝|玩转ETF|九点特供|点金互动易|研选[•·]研报数据)】/;
const teaserWording = /(?:这家(?:公司|企业)|该(?:行业|领域)|分析师强call)/i;

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
  const featured =
    Boolean(message.event_id) ||
    !(
      (topics.length > 0 &&
        topics.every((name) => name === "盘面直播") &&
        routineBroadcast.test(title)) ||
      (topics.length === 0 &&
        columnTeaser.test(originalTitle) &&
        teaserWording.test(originalTitle))
    );
  const category =
    macroHeadline.test(title) || topics.some((name) => macroTopics.has(name))
      ? categories.macro
      : disclosureHeadline.test(title)
        ? categories.disclosure
        : industryHeadline.test(title)
          ? categories.industry
          : marketHeadline.test(title) ||
              topics.some((name) => marketTopics.has(name))
            ? categories.market
            : topics.some((name) => industryTopics.has(name))
              ? categories.industry
              : categories.other;

  // Keep the latest quote per direction so a reversal stays visible.
  const quoteKey = regularQuote
    ? `${regularQuote[1].toUpperCase()}:${regularQuote[2] ? "up" : "down"}`
    : undefined;
  return { ...category, featured, quoteKey };
}
