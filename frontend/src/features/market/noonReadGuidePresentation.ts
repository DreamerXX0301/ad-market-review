export type NoonReadingTab = "overview" | "previous" | "gaps";
export type NoonReadingSection = {
  id: string;
  tab: NoonReadingTab;
  step: string | null;
  title: string;
  question: string;
  detail: string;
};

/** A reading route changes only the local tab and scroll position. */
export const NOON_READING_SECTIONS: readonly NoonReadingSection[] = [
  {
    id: "noon-overview-start",
    tab: "overview",
    step: "01",
    title: "看整体",
    question: "指数和市场广度是否同向？",
    detail: "先看指数、上涨范围和成交额，建立午间市场背景。",
  },
  {
    id: "noon-prior-feedback",
    tab: "overview",
    step: "02",
    title: "昨日接力",
    question: "昨天的强势股今天表现如何？",
    detail: "分开看昨日首板与连板，再查看回落、涨停和缺失样本。",
  },
  {
    id: "noon-today-ladder",
    tab: "overview",
    step: "03",
    title: "今日梯队",
    question: "当前各高度有哪些股票？",
    detail: "看本次涨停池的高度、股票和来源题材，不提前判定收盘晋级。",
  },
  {
    id: "noon-directions",
    tab: "overview",
    step: "04",
    title: "主线方向",
    question: "哪些方向走强或转弱？",
    detail: "左侧选板块，右侧看午间成员；名称看K线，昨日原关注单独对照。",
  },
  {
    id: "noon-afternoon",
    tab: "overview",
    step: "05",
    title: "午后观察",
    question: "下午具体核对哪些对象？",
    detail: "把具体对象、午间事实与后续核对条件放在一起看。",
  },
  {
    id: "noon-review-history",
    tab: "previous",
    step: null,
    title: "昨日原复盘",
    question: "昨天实际保存了什么判断？",
    detail: "查看当时留存的原文和版本，缺少的判断不补写。",
  },
];

export function noonReadingSection(id: unknown): NoonReadingSection | null {
  return typeof id === "string"
    ? NOON_READING_SECTIONS.find((section) => section.id === id) || null
    : null;
}

export const NOON_READING_TABS: Readonly<Record<string, NoonReadingTab>> =
  Object.fromEntries(NOON_READING_SECTIONS.map(({ id, tab }) => [id, tab]));
