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
const conditions = await import(
  await moduleUrl(new URL("./Conditions.tsx", import.meta.url))
);
const tree = await import(
  await moduleUrl(new URL("./RuleTreeEditor.tsx", import.meta.url))
);
const pathEditor = await import(
  await moduleUrl(new URL("./HistoryPricePathEditor.tsx", import.meta.url))
);
const evidence = await import(
  await moduleUrl(new URL("./RuleEvidence.tsx", import.meta.url))
);
const unchanged = () => {
  throw new Error("SSR cannot change a saved rule or business state");
};
const fields = [
  "close",
  "volume_ratio",
  "change_pct",
  "limit_up",
  "macd_cross",
  "macd_dif",
  "macd_dea",
  "kd_cross",
  "kdj_k",
  "kdj_d",
].map((field) => ({
  field,
  type: field.endsWith("cross") || field === "limit_up" ? "boolean" : "number",
  operators: [">", ">=", "<", "<=", "=="],
  historical_event_supported: true,
  supported_periods: ["D", "W", "M"],
}));
const old = { field: "close", op: "<", right_event_field: "close" };
const rangeHtml = renderToStaticMarkup(
  createElement(tree.default, {
    value: { all: [{ field: "kd_cross", op: "==", value: true, period: "D" }] },
    fields: ["kd_cross", "kdj_k"].map((field) => ({
      field,
      type: field === "kd_cross" ? "boolean" : "number",
      operators: ["==", ">=", "<="],
      supported_periods: ["D"],
    })),
    period: "D",
    onChange: unchanged,
  }),
);
assert.match(rangeHtml, /快捷添加条件/);
assert.match(rangeHtml, /aria-expanded="false"/);
assert.doesNotMatch(rangeHtml, /aria-label="K值范围下限"/);
assert.doesNotMatch(rangeHtml, /aria-label="快捷条件模板"/);
const oldBefore = JSON.stringify(old);
const oldHtml = renderToStaticMarkup(
  createElement(conditions.default, {
    conditions: [old],
    fields,
    period: "M",
    allowEventReferences: true,
    onChange: unchanged,
  }),
);
assert.doesNotMatch(oldHtml, /aria-label="事件价偏移百分比"/);
assert.equal(JSON.stringify(old), oldBefore);
for (const pct of [-3, 0, 3, 10000, ""]) {
  const leaf = { ...old, period: "D", right_event_change_pct: pct };
  const html = renderToStaticMarkup(
    createElement(conditions.default, {
      conditions: [leaf],
      fields,
      period: "M",
      allowEventReferences: true,
      onChange: unchanged,
    }),
  );
  assert.match(html, /事件价偏移 \(%\)/);
  assert.match(html, /aria-label="事件价偏移百分比"/);
  assert.match(html, /不是今日涨幅/);
  assert.match(html, /两条AND条件限定±3%/);
  if (pct === "") assert.match(html, /role="alert"/);
}
for (const indicator of ["macd", "kdj"]) {
  const value = { any: [rules.newPreviousCrossRule(indicator)] };
  const before = JSON.stringify(value);
  const html = renderToStaticMarkup(
    createElement(tree.default, {
      value,
      fields,
      period: "M",
      onChange: unchanged,
    }),
  );
  assert.match(html, /新增历史规则模板/);
  assert.equal(
    (html.match(/aria-label="新增历史规则模板"/g) || []).length,
    1,
    "no nested history insertion inside where or then",
  );
  assert.match(html, /MACD本次金叉价高于上次/);
  assert.match(html, /KDJ本次金叉价高于上次/);
  assert.match(html, /涨幅事件/);
  assert.match(html, /倍量 \/ 涨停 \/ 涨幅任一/);
  assert.match(html, /近30个交易日/);
  assert.match(html, /近60个交易日/);
  assert.match(html, /KD或MACD金叉（OR）/);
  assert.match(html, /金叉→死叉→再金叉/);
  assert.match(html, /窗口内/);
  assert.equal((html.match(/快捷添加条件<\/button>/g) || []).length, 3);
  assert.doesNotMatch(html, /aria-label="快捷条件模板"/);
  assert.equal(JSON.stringify(value), before);
  const invalid = { ...value.any[0].history_event, match: "earliest" };
  const invalidBefore = JSON.stringify(invalid);
  const invalidHtml = renderToStaticMarkup(
    createElement(pathEditor.default, { event: invalid, onChange: unchanged }),
  );
  assert.match(invalidHtml, /role="alert"/);
  assert.match(invalidHtml, /历史条件只保留同类日K金叉/);
  assert.equal(JSON.stringify(invalid), invalidBefore);
}

const comparison = {
  ...old,
  period: "D",
  right_event_change_pct: 3,
  actual: 10.2,
  event_price: 10,
  right_actual: 10.3,
  threshold_decimal: "10.300000000000000000000000000000000001",
  event_change_pct: 2,
  event_change_pct_decimal: "2",
  result: true,
};
const priceHtml = renderToStaticMarkup(
  createElement(evidence.EventPriceOffsetEvidence, { value: comparison }),
);
assert.match(priceHtml, /原事件参照价/);
assert.match(priceHtml, /1 \+ 3\/100/);
assert.match(priceHtml, /title="10\.300000000000000000000000000000000001"/);
assert.match(priceHtml, /2%/);
const decimalOnly = renderToStaticMarkup(
  createElement(evidence.EventPriceOffsetEvidence, {
    value: { ...comparison, right_actual: null },
  }),
);
assert.match(
  decimalOnly,
  />10\.300000000000000000000000000000000001<\/span>/,
  "exact threshold remains visible if the optional float cannot safely represent it",
);
for (const [dead_cross_result, expected] of [
  [true, "满足"],
  [false, "未满足"],
  [null, "待核验"],
]) {
  const html = renderToStaticMarkup(
    createElement(evidence.HistoryPricePathEvidence, {
      value: {
        result: false,
        cross_cycle: {
          indicator: "macd",
          result: false,
          event_cross: true,
          current_cross: true,
          dead_cross_result,
          previous_cross_confirmed: true,
          known_signal_sessions: 2,
          expected_sessions: 3,
          event_close: 60,
          current_close: 61,
        },
      },
    }),
  );
  assert.match(html, /MACD 金叉→死叉→再金叉/);
  assert.match(html, new RegExp(`<dt>中间确实死叉<\/dt><dd>${expected}`));
  assert.match(html, /价格高低|事件价比较、百分比偏移/);
  assert.match(html, /今日K值范围/);
}
const text = conditions.summarizeChecks([comparison]);
assert.match(text, /阈值 10\.300000000000000000000000000000000001/);
assert.match(text, /实际相对事件价涨跌 2%/);
assert.doesNotMatch(
  conditions.summarizeChecks([
    { ...old, actual: 10, right_actual: 11, result: true },
  ]),
  /阈值|偏移/,
);
console.log(
  "historical cross presentation: pure-template menu, OR signal shortcuts, signed offset controls, exact thresholds, strict-cycle errors and independent signal evidence passed",
);
