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
  conditionsOf,
} = await import(await moduleUrl(new URL("./Conditions.tsx", import.meta.url)));
const { default: RuleEvidence } = await import(
  await moduleUrl(new URL("./RuleEvidence.tsx", import.meta.url))
);
const leaf = {
  field: "boll_signal",
  op: "matches",
  value: { ...rules.BOLL_DEFAULTS },
};
const unchanged = () => {
  throw new Error("Rendering must not change saved rules");
};
assert.equal(
  rules.bollConfigError({}),
  null,
  "missing parameters use defaults",
);
assert.deepEqual(rules.bollConfig({ band_period: 30 }), {
  ...rules.BOLL_DEFAULTS,
  band_period: 30,
});
for (const value of [null, [], "20", 20, true])
  assert(rules.bollConfigError(value));
for (const [key, values] of [
  ["band_period", ["20", "", 1, 251, 2.5, true, null, NaN]],
  ["rank_window", [9, 751, 10.5]],
  ["recent_squeeze_periods", [0, 61, 1.5]],
  ["slope_periods", [0, 61, 2.5]],
  ["deviations", [0, 0.09, 10.01, Infinity, "2", false]],
  ["squeeze_percentile", [0, 100.01, Infinity, "10"]],
  ["mode", ["crossover", null, 1]],
  ["middle_direction", ["up", null, false]],
  ["require_squeeze", ["true", null, 1]],
  ["require_expansion", ["false", null, 0]],
])
  for (const value of values)
    assert(
      rules.bollConfigError({ [key]: value }),
      `${key}=${value} must be rejected`,
    );
assert(rules.bollConfigError({ ...leaf.value, future_return: 10 }));
for (const [key, values] of [
  ["band_period", [2, 250]],
  ["rank_window", [10, 750]],
  ["recent_squeeze_periods", [1, 60]],
  ["slope_periods", [1, 60]],
  ["deviations", [0.1, 10]],
  ["squeeze_percentile", [1, 10.5, 100]],
])
  for (const value of values)
    assert.equal(rules.bollConfigError({ [key]: value }), null);
for (const bad of [
  { ...leaf, op: ">=" },
  { ...leaf, right_field: "close" },
  { ...leaf, right_event_field: "high" },
  { ...leaf, right_event_change_pct: 0 },
])
  assert(rules.validateVisualRule(bad));
assert.match(rules.validateVisualRule(leaf, "where"), /不作为历史事件日筛选/);
assert.equal(rules.validateVisualRule(leaf, "then"), null);
const combo = {
  all: [
    leaf,
    { field: "volume_ratio", op: ">=", value: 1.5 },
    {
      any: [
        { field: "macd_cross", op: "==", value: true },
        { field: "kd_cross", op: "==", value: true },
      ],
    },
  ],
};
assert.equal(rules.validateVisualRule(combo), null);
assert.match(
  rules.historyRuleSummary(combo),
  /BOLL.*且.*volume_ratio.*且.*macd_cross.*或.*kd_cross/,
);
assert.deepEqual(conditionsOf({ all: [leaf] }), [leaf]);
assert.equal(
  fallbackFields.find((f) => f.field === "boll_signal")
    .historical_event_supported,
  false,
);
for (const period of ["D", "W", "M"]) {
  const rule = { ...leaf, period };
  const before = JSON.stringify(rule);
  const html = renderToStaticMarkup(
    createElement(Conditions, {
      conditions: [rule],
      fields: fallbackFields,
      onChange: unchanged,
    }),
  );
  for (const label of [
    "判断信号",
    "BOLL周期（根）",
    "标准差倍数",
    "中轨方向",
    "此前收口窗口（期）",
    "分位回看窗口（期）",
    "收口分位（%）",
    "要求信号前出现收口",
    "当期带宽较前期扩大",
  ])
    assert(html.includes(label), label);
  assert.match(html, /min="2" max="250" step="1"/);
  assert.match(html, /min="0.1" max="10" step="any"/);
  assert.match(html, /收盘确认/);
  assert.match(html, /只用该期之前的带宽/);
  assert.match(html, /放量、MACD、KDJ/);
  assert.doesNotMatch(html, /aria-label="比较关系"|aria-label="比较对象"/);
  assert.equal(JSON.stringify(rule), before);
  assert.equal(rules.validateVisualRule(rule), null);
}
for (const mode of rules.BOLL_MODES) {
  const node = { ...leaf, value: { ...leaf.value, mode: mode.value } };
  assert(rules.historyRuleSummary(node).includes(mode.label));
  assert.doesNotMatch(
    rules.historyRuleSummary(node),
    /\[object Object\]|matches/,
  );
  const html = renderToStaticMarkup(
    createElement(Conditions, {
      conditions: [node],
      fields: fallbackFields,
      period: "W",
      onChange: unchanged,
    }),
  );
  assert(html.includes(mode.description));
  if (mode.value === "squeeze") {
    assert.doesNotMatch(html, /要求信号前出现收口|此前收口窗口（期）/);
    assert(html.includes("分位回看窗口（期）"));
  }
}
const filterNode = {
  ...leaf,
  value: {
    ...leaf.value,
    require_squeeze: false,
    middle_direction: "rising",
    slope_periods: 8,
    require_expansion: true,
  },
};
const filterSummary = rules.historyRuleSummary(filterNode);
assert.match(filterSummary, /中轨较8期前上行/);
assert.match(filterSummary, /带宽较前期扩大/);
assert.doesNotMatch(filterSummary, /分位/);
const filterHtml = renderToStaticMarkup(
  createElement(Conditions, {
    conditions: [filterNode],
    fields: fallbackFields,
    onChange: unchanged,
  }),
);
assert(filterHtml.includes("中轨比较间隔（期）"));
assert(!filterHtml.includes("分位回看窗口（期）"));
const invalidHtml = renderToStaticMarkup(
  createElement(Conditions, {
    conditions: [{ ...leaf, value: { ...leaf.value, band_period: "" } }],
    onChange: unchanged,
  }),
);
assert.match(invalidHtml, /role="alert"/);
assert.match(invalidHtml, /修正后才能保存/);
for (const result of [true, false, null]) {
  const text = summarizeChecks({
    ...leaf,
    result,
    reason: result == null ? "预热不足" : "",
    observed_date: "2026-10-23",
  });
  assert.match(text, /BOLL/);
  assert(
    text.includes(
      result === true ? "满足" : result === false ? "未满足" : "待核验",
    ),
  );
  assert.doesNotMatch(text, /\[object Object\]|matches/);
}
const proof = {
  ...leaf,
  result: true,
  observed_date: "2026-10-23",
  covered_periods: 150,
  required_periods: 145,
  evidence: {
    period: "D",
    close: 12,
    upper: 11.8,
    middle: 10.8,
    lower: 9.8,
    bandwidth_pct: 18.5185,
    historical_threshold_pct: 10.5,
    historical_width_count: 125,
    squeeze: false,
    recent_squeeze: true,
    recent_squeeze_date: "2026-10-20",
    previous_close: 11.5,
    previous_upper: 11.7,
    previous_lower: 9.7,
    middle_change_pct: 0.5,
    expanding: true,
  },
};
const proofHtml = renderToStaticMarkup(
  createElement(RuleEvidence, { value: { checks: [proof] } }),
);
assert.match(proofHtml, /查看指标依据|BOLL 信号依据/);
assert.match(proofHtml, /18.5185%/);
assert.match(proofHtml, /10.5000%/);
assert.match(proofHtml, /2026-10-20/);
assert.match(proofHtml, /预热覆盖/);
assert(!proofHtml.includes("boll-signal-v1"));
const unknownHtml = renderToStaticMarkup(
  createElement(RuleEvidence, {
    value: { ...leaf, result: null, reason: "预热不足" },
  }),
);
assert.match(unknownHtml, /待核验/);
assert.match(unknownHtml, /预热不足/);
assert(!unknownHtml.includes("undefined"));
console.log(
  "BOLL strategy parameters, composition, summaries, evidence and rendering: passed",
);
