import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    fileName: path.pathname,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [
    ...source.matchAll(/(?:from |import )(["'])([^"']+)\1/g),
  ]) {
    const name = match[2];
    if (name.endsWith(".css")) {
      source = source.replace(match[0], "void " + JSON.stringify(name));
      continue;
    }
    let target;
    if (name.endsWith("TopicSelect"))
      target =
        "data:text/javascript,export default function TopicSelect(){return null}";
    else if (name.startsWith(".")) {
      const local = new URL(name + ".ts", path);
      try {
        await fs.access(local);
      } catch {
        local.pathname += "x";
      }
      target = await moduleUrl(local);
    } else target = import.meta.resolve(name);
    source = source.replace(
      match[0],
      match[0].replace(match[1] + name + match[1], JSON.stringify(target)),
    );
  }
  const url =
    "data:text/javascript;base64," + Buffer.from(source).toString("base64");
  modules.set(path.href, url);
  return url;
}
const rules = await import(
  await moduleUrl(new URL("./historicalRules.ts", import.meta.url))
);
const {
  default: Conditions,
  fallbackFields,
  summarizeChecks,
} = await import(await moduleUrl(new URL("./Conditions.tsx", import.meta.url)));
const { conditionFieldGroup } = await import(
  await moduleUrl(new URL("./conditionScope.ts", import.meta.url))
);
const { default: RuleEvidence } = await import(
  await moduleUrl(new URL("./RuleEvidence.tsx", import.meta.url))
);
const leaf = { field: "higher_lows", op: "confirmed_rising_lows", value: 60 };
const unchanged = () => {
  throw new Error("Rendering must not change a saved strategy");
};
for (const [period, unit] of [
  ["D", "个交易日"],
  ["W", "根完整周K"],
  ["M", "根完整月K"],
]) {
  const value = { ...leaf, period };
  const before = JSON.stringify(value);
  const html = renderToStaticMarkup(
    createElement(Conditions, {
      conditions: [value],
      fields: fallbackFields,
      period: "D",
      onChange: unchanged,
    }),
  );
  assert.match(html, /底部抬高回看周期数/);
  assert.match(html, /min="8" max="750" step="1"/);
  assert.match(html, /value="60"/);
  assert(html.includes(unit));
  assert.doesNotMatch(html, /aria-label="比较关系"/);
  assert.doesNotMatch(html, /aria-label="比较对象"/);
  assert.match(html, /左右各2根确认/);
  assert.match(html, /不自动限定两底发生在历史事件之后/);
  assert.equal(JSON.stringify(value), before);
  assert(rules.historyRuleSummary(value).includes(unit));
  assert.equal(rules.validateVisualRule(value), null);
  assert.equal(rules.validateVisualRule(value, "then"), null);
  assert.match(
    rules.validateVisualRule(value, "where"),
    /不作为历史事件日筛选/,
  );
}
const inheritedHtml = renderToStaticMarkup(
  createElement(Conditions, {
    conditions: [leaf],
    fields: fallbackFields,
    period: "W",
    onChange: unchanged,
  }),
);
assert(inheritedHtml.includes("根完整周K"));
assert.equal(
  rules.validateVisualRule({
    all: [
      rules.newHistoryRule(),
      leaf,
      {
        any: [
          { field: "macd_dif", op: ">=", right_field: "macd_dea", period: "W" },
          { field: "kdj_k", op: ">=", right_field: "kdj_d", period: "W" },
        ],
      },
    ],
  }),
  null,
);
assert.match(
  rules.historyRuleSummary({ all: [rules.newHistoryRule(), leaf] }),
  /底部抬高/,
);
assert.doesNotMatch(rules.historyRuleSummary(leaf), /confirmed_rising_lows/);
assert.equal(
  conditionFieldGroup(
    fallbackFields.find((x) => x.field === "higher_lows"),
    "trade-entry",
  ),
  "推荐 · 上行与突破",
);
for (const invalid of ["", 7, 751, 60.5, "60", null, true, Infinity, NaN])
  assert(rules.validateVisualRule({ ...leaf, value: invalid }));
for (const invalid of [
  { ...leaf, op: ">=" },
  { ...leaf, right_field: "close" },
  { ...leaf, right_event_field: "close" },
])
  assert(rules.validateVisualRule(invalid));
const emptyHtml = renderToStaticMarkup(
  createElement(Conditions, {
    conditions: [{ ...leaf, value: "" }],
    fields: fallbackFields,
    period: "D",
    onChange: unchanged,
  }),
);
assert.match(emptyHtml, /role="alert"/);
for (const result of [true, false, null]) {
  const text = summarizeChecks([
    {
      ...leaf,
      period: "D",
      result,
      actual: result,
      reason: result === null ? "报价缺口" : "",
    },
  ]);
  assert.match(text, /底部抬高/);
  assert(!text.includes("confirmed_rising_lows"));
  assert(
    text.includes(
      result === true ? "满足" : result === false ? "未满足" : "待核验",
    ),
  );
}
const lowEvidence = {
  ...leaf,
  result: true,
  observed_date: "2026-10-23",
  evidence: {
    recent_lows: [
      { date: "2026-09-08", confirmed_on: "2026-09-10", price: 10 },
      { date: "2026-10-13", confirmed_on: "2026-10-15", price: 11 },
    ],
    minimum_close_since_l2_confirmation: 11.5,
  },
};
const evidenceHtml = renderToStaticMarkup(
  createElement(RuleEvidence, { value: { all: [lowEvidence] } }),
);
for (const text of [
  "查看低点依据",
  "L1",
  "L2",
  "2026-09-08",
  "2026-09-10",
  "10.00",
  "2026-10-13",
  "2026-10-15",
  "11.00",
  "11.50",
  "2026-10-23",
])
  assert(evidenceHtml.includes(text));
assert.doesNotMatch(evidenceHtml, /confirmed_rising_lows/);
const unknownHtml = renderToStaticMarkup(
  createElement(RuleEvidence, {
    value: {
      ...leaf,
      result: null,
      evidence: { recent_lows: [] },
      reason: "行情缺口",
    },
  }),
);
assert.match(unknownHtml, /待核验/);
assert.match(unknownHtml, /暂无可确认的低点依据/);
assert.doesNotMatch(unknownHtml, /0\.00/);
console.log(
  "higher lows presentation: adjustable D/W/M windows, isolated parameters, no history-where, safe summaries and nested combinations",
);
