import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (code) =>
  "data:text/javascript;base64," + Buffer.from(code).toString("base64");
const source = await fs.readFile(
  new URL("./GainerDistribution.tsx", import.meta.url),
  "utf8",
);
let code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;
code = code.replace('import "./GainerDistribution.css";', "");
for (const [name, url] of Object.entries({
  react: import.meta.resolve("react"),
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
  "../../lib/format": encode(
    'export const integer=value=>value==null?"—":Number(value).toLocaleString("zh-CN");',
  ),
}))
  code = code.replaceAll('from "' + name + '"', "from " + JSON.stringify(url));
const { default: GainerDistribution, distributionRows } = await import(
  encode(code)
);
const top = Array.from({ length: 20 }, (_, index) => ({
  id: "source-" + index,
  topic_id: "t" + index,
  name: "分类" + index,
  count: 1000 - index,
  share_pct: 100 - index / 10,
  source: "fixture",
}));
assert.equal(distributionRows(top).length, 5);
assert.equal(
  distributionRows([
    { ...top[0], share_pct: Infinity },
    { ...top[0], count: NaN },
    { ...top[0], share_pct: 101 },
  ]).length,
  0,
);
const data = {
  date: "2026-11-02",
  sample_limit: 1000,
  sample_count: 1000,
  topics: { top, classified_count: 999, unknown_count: 0, attribute_only_count: 1,
    definition: "经济/政策/产业题材分布，不含已审定交易资格标签" },
  industries: { top: [] },
  excluded_attributes: { unique_stock_count: 200 },
};
const html = renderToStaticMarkup(createElement(GainerDistribution, { data }));
assert.equal((html.match(/<li/g) || []).length, 5);
assert.ok(html.includes("分类4") && !html.includes("分类5"));
assert.ok(html.includes("题材") && html.includes("行业"));
assert.ok(html.includes("原始涨幅排名前") && html.includes("1,000"));
assert.ok(html.includes("合计可能超过 100%") && !html.includes("其他占比"));
assert.ok(html.includes("未计入融资融券、沪深股通等交易资格标签"));
assert.ok(html.includes("仅资格标签 1 只"));
const empty = renderToStaticMarkup(
  createElement(GainerDistribution, {
    data: {
      ...data,
      sample_count: 0,
      topics: { top: [], classified_count: 0, unknown_count: 0 },
    },
  }),
);
assert.ok(empty.includes("所选日没有可排名样本"));
assert.ok(!empty.includes("0.0%"));
const duplicate = renderToStaticMarkup(
  createElement(GainerDistribution, {
    data: {
      ...data,
      topics: {
        top: [
          { ...top[0], name: "同名", source_code: "provider-a" },
          { ...top[1], name: "同名", source_code: "provider-b" },
        ],
      },
    },
  }),
);
assert.ok(
  duplicate.includes("同名 · provider-a") &&
    duplicate.includes("同名 · provider-b"),
);
console.log(
  "Gainer distribution: compact top-five rendering, identity labels, sample basis and unknown/overlap captions passed",
);
