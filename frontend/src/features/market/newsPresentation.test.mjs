import assert from "node:assert/strict";
import {
  calendarImportance,
  classifyNews,
  selectFeaturedNews,
} from "./newsPresentation.ts";

const entry = (title, event_id) => ({
  title,
  category: classifyNews({ title, event_id }),
});

const rise = entry("日经225指数上涨3.2%，报41000点。");
const oldRise = entry("日经225指数上涨2.9%，报40900点。");
const fall = entry("日经225指数下跌3.5%，报40000点。");
const policy = entry("国务院：暂停加征24%的关税");
const routine = entry("隔夜例行天气预报");
assert.equal(rise.category.move?.direction, "up");
assert.equal(fall.category.move?.direction, "down");
assert.equal(policy.category.move, undefined);
assert.equal(entry("美国10年期国债收益率上涨5%").category.move, undefined);
assert.equal(entry("某甲公司预计股价将大涨").category.move, undefined);
assert.equal(entry("某甲公司涨停，某乙公司跌停").category.move, undefined);
assert.deepEqual(selectFeaturedNews([routine, rise, oldRise, fall, policy]), [
  rise,
  fall,
  policy,
]);

// Even a low priority headline linked to a verified calendar event survives
// the daily cap, and the selected results retain the original time order.
const linked = entry("相关日程公告", "verified-calendar-event");
const many = Array.from({ length: 40 }, (_, i) =>
  entry(`国家统计局：公布第${i + 1}期数据`),
);
const timeline = [rise, ...many, linked, routine];
assert.deepEqual(selectFeaturedNews(timeline), [...many.slice(0, 29), linked]);
assert.equal(timeline.length, 43);
const eventHeavyDay = Array.from({ length: 31 }, (_, i) =>
  entry("相关日程公告", `event-${i}`),
);
assert.deepEqual(selectFeaturedNews([rise, ...eventHeavyDay]), eventHeavyDay);

assert.deepEqual(entry("英国政府宣布下调部分进口税").category.filters, [
  "policy",
  "overseas",
]);
assert.deepEqual(
  classifyNews({
    title: "布伦特原油期货大跌超5%",
    topics: [{ name: "环球市场情报" }],
  }).filters,
  ["futures", "overseas"],
);
assert.deepEqual(entry("英伟达股价大跌6%").category.filters, [
  "overseas",
  "stocks",
]);
assert.deepEqual(entry("上市公司年内增持回购统计").category.filters, ["other"]);
assert.deepEqual(entry("半导体板块盘中拉升 某甲科技涨停").category.filters, [
  "stocks",
  "industry",
]);

assert.equal(policy.category.importance?.label, "重要");
const officialData = entry("国家统计局：10月份居民消费价格同比上涨0.7%");
assert.equal(
  officialData.category.importance?.reason,
  "标题载明官方核心经济数据",
);
const rateDecision = entry("美联储宣布下调联邦基金利率目标区间25个基点");
assert.equal(rateDecision.category.importance?.kind, "macro");
const halt = classifyNews({
  title: "某甲地产：股票停牌 以待刊发有关内幕消息的公告",
  topics: [{ name: "期货市场情报" }],
});
assert.equal(halt.importance?.kind, "disclosure");
assert.equal(halt.key, "disclosure");
assert.ok(halt.filters.includes("stocks"));

for (const title of [
  "国家统计局：10月汽油价格同比上涨22.4%",
  "央行今日开展1820亿元隔夜逆回购操作",
  "美联储12月加息的概率为78%",
  "国务院：公布《某项条例（征求意见稿）》",
  "商务部：公布出口管制措施答记者问",
  "【公告】公司澄清未筹划重大资产重组",
  "【公告】重大资产重组问询函回复",
  "某甲科技大涨15%",
]) {
  assert.equal(entry(title).category.importance, undefined, title);
}
assert.equal(rise.category.importance, undefined);
assert.deepEqual(selectFeaturedNews([...many, officialData]), [
  ...many.slice(0, 29),
  officialData,
]);

const calendar = {
  title: "采购经理指数月度报告",
  source: "国家统计局",
  status: "tentative",
};
assert.equal(calendarImportance(calendar)?.label, "重点日程");
assert.equal(
  calendarImportance({ ...calendar, status: "cancelled" }),
  undefined,
);
assert.equal(
  calendarImportance({ ...calendar, source: "其他来源" }),
  undefined,
);
assert.equal(
  calendarImportance({ ...calendar, title: "例行工作会议" }),
  undefined,
);
