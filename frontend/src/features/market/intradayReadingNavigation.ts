export const intradayReadingSections = {
  brief: { label: "累计简报", description: "从竞价到当前时点的重点与变化" },
  discovery: { label: "竞价发现", description: "本次竞价中的行业、题材和个股" },
  market: { label: "市场概况", description: "指数、市场广度及可比成交额" },
  changes: {
    label: "本段板况变化",
    description: "新增封板、开板、回封与跌停变化",
  },
  ladder: { label: "封板梯队", description: "当前核验的封板高度、行业与题材" },
  broken: { label: "炸板观察", description: "炸板股票及对应行业、题材" },
  prior: {
    label: "前日反馈",
    description: "前一交易日涨停与炸板样本的本轮表现",
  },
  sameDay: {
    label: "同日封板变化",
    description: "同日两份快照中的封板状态对照",
  },
  height: { label: "高度板参考", description: "前日高度板的竞价反馈" },
  directions: { label: "盘中方向", description: "行业、题材及相应成员个股" },
  legacyRankings: {
    label: "涨幅排行",
    description: "旧快照已保留的板块和个股涨幅",
  },
  retrospective: { label: "全天回看", description: "正式收盘后的全天变化" },
  rotation: { label: "题材轮动", description: "各时点题材表现与相邻时点变化" },
  indexQuotes: { label: "指数概览", description: "各指数的所选日行情" },
  indexChart: { label: "指数走势", description: "当前指数的历史K线" },
  marketRanks: { label: "个股榜单", description: "成交额与涨幅前列" },
  sealRanks: { label: "封板与炸板", description: "炸板样本及封单金额榜单" },
  ladderSummary: { label: "梯队概况", description: "封板样本和最高连板" },
  ladderDetail: {
    label: "梯队明细",
    description: "当前视图的梯队、题材或当日主线",
  },
  ladderEvents: { label: "补充事件", description: "其他涨停及事件分类" },
  nodeReplay: { label: "节点回放", description: "回放日期与反馈截止日" },
  nodePrior: {
    label: "前日梯队对照",
    description: "最高和次高梯队的量价与节点日反馈",
  },
  nodeTopics: {
    label: "行业与概念梯队",
    description: "节点日行业和概念的各板高样本",
  },
  nodeFeedback: {
    label: "原名单与反馈",
    description: "首板至三板同批样本的后续晋级和断板",
  },
  sources: { label: "数据与来源", description: "本页覆盖范围与数据口径" },
  dataSummary: { label: "数据概况", description: "数据水位与已发布文件" },
  automatic: { label: "自动计划", description: "竞价、盘中、午评与盘后复盘" },
  syncActions: { label: "同步操作", description: "初始化、盘后增量及缺口修补" },
  task: { label: "当前任务", description: "查看执行进度及相关步骤" },
  dataCoverage: { label: "数据覆盖", description: "查看各数据集实际覆盖" },
  taskHistory: { label: "同步历史", description: "查看近期执行与缺口" },
} as const;

export type IntradayReadingKey = keyof typeof intradayReadingSections;

/** Applied only by the actual rendered top-level cards, never their inner sections. */
export function intradayReadingProps(key: IntradayReadingKey) {
  return {
    id: `intraday-reading-${key}`,
    "data-intraday-reading": key,
  };
}

/** Keep every horizontal position intact when moving the page vertically. */
export function intradayReadingScrollTop(
  targetTop: number,
  currentTop: number,
  activationTop: number,
) {
  return Math.max(0, currentTop + targetTop - activationTop);
}

export function isIntradayPageScroll(
  target: EventTarget | null,
  document: Document,
  owner: HTMLElement | null,
) {
  return (
    target === document ||
    target === document.documentElement ||
    target === document.body ||
    (owner !== null && target === owner)
  );
}
