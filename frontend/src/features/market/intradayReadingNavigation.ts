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
