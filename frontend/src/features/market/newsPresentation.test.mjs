import assert from "node:assert/strict";
import { classifyNews, selectFeaturedNews } from "./newsPresentation.ts";

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
