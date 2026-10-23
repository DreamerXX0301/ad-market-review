import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = (text, tsx = false) =>
  ts
    .transpileModule(text, {
      fileName: tsx ? "activity.tsx" : "activity.ts",
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    .outputText.replaceAll(
      '"react/jsx-runtime"',
      JSON.stringify(import.meta.resolve("react/jsx-runtime")),
    );
const helper = encode(
  compile(
    await fs.readFile(
      new URL("./amountActivityPresentation.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const { amountActivityView: view, amountActivityDirections: directions } =
  await import(helper);
const complete = {
  count: 3,
  dates: ["2026-10-22", "2026-10-21", "2026-10-16"],
  ranks: [
    { date: "2026-10-21", rank: 4, amount: 20 },
    { date: "2026-10-22", rank: 2, amount: 30 },
  ],
  observed_days: 7,
  expected_days: 7,
  complete: true,
  state: "complete",
  window_start: "2026-10-14",
  window_end: "2026-10-22",
};
assert.equal(view(complete).label, "近7日上榜3次");
assert.equal(view(complete).repeated, true);
assert.deepEqual(
  view(complete).details.map((row) => row.rank),
  [2, 4],
);
assert.deepEqual(
  complete.ranks.map((row) => row.rank),
  [4, 2],
  "source arrays remain unchanged",
);
const partial = {
  ...complete,
  count: 2,
  observed_days: 4,
  complete: false,
  state: "observed_partial",
  reason: "缺三日榜单",
};
assert.equal(view(partial).label, "近7日已观察2次");
assert.equal(view(partial).coverage, "已观察4/7日 · 样本不完整");
assert.equal(
  view({ ...partial, observed_days: 7 }).coverage,
  "已观察7/7日 · 样本不完整",
);
assert.equal(
  view({ ...complete, observed_days: 4 }).complete,
  false,
  "complete flag cannot override fewer than seven days",
);
assert.equal(view(null).count, null);
assert.equal(view(null).label, "上榜次数待核验");
for (const count of [null, undefined, "2", -1, 8, Infinity, NaN])
  assert.equal(view({ ...partial, count }).count, null);
assert.equal(view({ ...complete, state: "unknown" }).count, null);
assert.equal(view({ ...complete, count: 1 }).repeated, false);
assert.equal(view({ ...partial, count: 0 }).label, "已观察样本未见");
assert.equal(
  view({
    ...complete,
    ranks: [
      { date: "2026-10-23", rank: 1 },
      { date: "2026-10-22", rank: 11 },
      { date: "2026-02-30", rank: 3 },
    ],
  }).details.length,
  0,
);
const topic = {
  topic_id: "t1",
  name: "测试行业",
  kind: "industry",
  observed_date: "2026-10-22",
  stock_count: 2,
  items: [{ instrument_id: "000001.SZ", name: "测试证券", count: 3 }],
};
const summary = {
  ...complete,
  directions: {
    state: "partial",
    items: [
      topic,
      {
        ...topic,
        topic_id: "future",
        name: "未来归属",
        observed_date: "2026-10-23",
      },
      { ...topic, topic_id: "missing", name: "无观察日", observed_date: null },
    ],
    reason: "其余股票缺成员日期",
  },
};
assert.deepEqual(
  directions(summary).map((row) => row.topic_id),
  ["t1"],
);
assert.deepEqual(
  directions({
    ...summary,
    directions: { ...summary.directions, state: "unknown" },
  }),
  [],
);
const source = (
  await fs.readFile(new URL("./AmountActivity.tsx", import.meta.url), "utf8")
)
  .replace('"./amountActivityPresentation"', JSON.stringify(helper))
  .replace('import "./AmountActivity.css";', "");
const { AmountActivityCell, AmountActivitySummary } = await import(
  encode(compile(source, true))
);
let html = renderToStaticMarkup(
  createElement(AmountActivityCell, { activity: complete }),
);
assert.ok(html.includes("近7日上榜3次") && html.includes("持续活跃"));
assert.ok(
  html.includes("<details") && !html.includes("<details open"),
  "dates stay collapsed by default",
);
assert.ok(html.includes("第2名") && html.includes("2026-10-22"));
html = renderToStaticMarkup(
  createElement(AmountActivityCell, { activity: partial }),
);
assert.ok(
  html.includes("已观察2次") && html.includes("已观察4/7日 · 样本不完整"),
);
assert.ok(
  !/[≥]|至少|下界/.test(html),
  "partial samples never claim a mathematical lower bound",
);
html = renderToStaticMarkup(createElement(AmountActivityCell, {}));
assert.ok(html.includes("上榜次数待核验") && !html.includes("上榜0次"));
html = renderToStaticMarkup(
  createElement(AmountActivitySummary, { summary, asOf: "2026-10-22" }),
);
assert.ok(html.includes("测试行业") && html.includes("题材观察日"));
assert.ok(!html.includes("未来归属") && !html.includes("无观察日"));
assert.ok(html.includes("不代表主线") && html.includes("题材之间可重叠"));
assert.ok(html.includes("其余股票缺成员日期"));
html = renderToStaticMarkup(createElement(AmountActivitySummary, {}));
assert.ok(html.includes("暂不汇总方向"));
const market = await fs.readFile(
  new URL("./Market.tsx", import.meta.url),
  "utf8",
);
assert.ok(market.includes('defaultSort={{ key: "amount", order: "desc" }}'));
assert.ok(market.includes("rows={(data?.top_amount || []).slice(0, 10)}"));
assert.ok(
  market.includes('key: "active_top10_7d",') &&
    market.includes("sortable: false"),
);
console.log(
  "amount TOP10 seven-day complete/observed-sample counts, folded rank dates, verified direction dates and unchanged daily selection passed",
);
