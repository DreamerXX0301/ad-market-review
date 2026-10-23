import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source) =>
  ts
    .transpileModule(source, {
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
      new URL("./stockMembershipPresentation.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const { stockCurrentMemberships } = await import(helper);
const detail = {
  instrument: { instrument_id: "600001.SH" },
  data_date: "2026-09-01",
  topics: [{ name: "观察日旧题材" }],
  current_topics_observed_date: "2026-10-22",
  current_topics_scope: "当前业务日归属，不代表历史原因",
  current_topics: [
    {
      topic_id: "c1",
      name: "现概念",
      kind: "concept",
      source_observed_date: "2026-10-21",
      members_complete: false,
    },
    {
      topic_id: "i1",
      name: "现行业",
      kind: "industry",
      source_observed_date: "2026-10-20",
      members_complete: true,
    },
    { topic_id: "m1", name: "手工研究", kind: "manual" },
    { topic_id: "c2", name: "另概念", kind: "concept" },
    { topic_id: "c3", name: "折叠概念", kind: "concept" },
    { topic_id: "c1", name: "现概念", kind: "concept" },
  ],
};
const original = JSON.stringify(detail);
for (const entry of [
  "gainers",
  "flow",
  "sector-members",
  "strategy",
  "tracking",
  "watchlist",
]) {
  const value = stockCurrentMemberships({ ...detail, entry }, "600001.SH");
  assert.equal(
    value.observed,
    "2026-10-22",
    "membership date stays independent of historical chart date",
  );
  assert.deepEqual(
    value.visible.map((tag) => tag.name),
    ["现行业", "现概念", "手工研究", "另概念"],
  );
  assert.deepEqual(
    value.additional.map((tag) => tag.name),
    ["折叠概念"],
  );
}
assert.deepEqual(
  stockCurrentMemberships(detail, "000001.SZ").visible,
  [],
  "previous stock's classifications cannot leak",
);
assert.equal(
  stockCurrentMemberships(
    { instrument_id: "600001.SH", topics: detail.topics },
    "600001.SH",
  ).known,
  false,
  "old observed topics are not mislabeled as current membership",
);
assert.equal(stockCurrentMemberships(null, "600001.SH").known, false);
assert.equal(
  stockCurrentMemberships({ ...detail, current_topics: [] }, "600001.SH").known,
  true,
);
assert.equal(
  JSON.stringify(detail),
  original,
  "display filtering preserves API response",
);
const source = (
  await fs.readFile(
    new URL("./StockCurrentTopics.tsx", import.meta.url),
    "utf8",
  )
)
  .replace('"./stockMembershipPresentation"', JSON.stringify(helper))
  .replace('import "./StockCurrentTopics.css";', "");
const { default: CurrentTopics } = await import(encode(compile(source)));
const html = renderToStaticMarkup(
  createElement(CurrentTopics, { data: detail, instrumentId: "600001.SH" }),
);
for (const text of [
  "当前归属",
  "关系有效日 2026-10-22",
  "来源采集日 2026-10-20",
  "非历史归因",
  "另 1 项",
])
  assert.ok(html.includes(text), text);
assert.ok(!html.includes("观察日旧题材"));
assert.ok(!html.includes("details open"));
assert.ok(
  renderToStaticMarkup(
    createElement(CurrentTopics, {
      data: { ...detail, current_topics: [] },
      instrumentId: "600001.SH",
    }),
  ).includes("不代表不属于任何分类"),
);
console.log(
  "stock memberships: shared entry presentation, independent current dates, source metadata, unknowns and identity isolation passed",
);
