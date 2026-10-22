import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const modules = new Map();
const compile = (source, fileName) =>
  ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = compile(await fs.readFile(path, "utf8"), path.pathname);
  const imports = [...source.matchAll(/(?:from |import )(["'])([^"']+)\1/g)];
  for (const match of imports) {
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
const conditions = await import(
  await moduleUrl(new URL("./Conditions.tsx", import.meta.url))
);
const tree = await import(
  await moduleUrl(new URL("./RuleTreeEditor.tsx", import.meta.url))
);
const trade = await import(
  await moduleUrl(new URL("./TradeRules.tsx", import.meta.url))
);
const fields = [
  { field: "close", type: "number", operators: [">"] },
  { field: "kdj_k", type: "number", operators: [">"] },
  { field: "kdj_d", type: "number", operators: [">"] },
  { field: "kdj_j", label: "KDJ J", type: "number", operators: [">"] },
  { field: "kd_cross", type: "boolean", operators: ["=="] },
  { field: "kd_dead_cross", type: "boolean", operators: ["=="] },
];
const onChange = () => {
  throw new Error("render must not mutate a rule");
};
for (const period of ["D", "W", "M"])
  for (const scope of [
    "selection",
    "alert",
    "exit",
    "trade-entry",
    "trade-exit",
  ]) {
    for (const rule of [
      { field: "close", op: ">", right_field: "kdj_k" },
      { field: "kdj_j", op: ">", value: 54.78 },
      { field: "kdj_k", op: ">", right_field: "kdj_j" },
    ]) {
      const snapshot = JSON.stringify(rule);
      const html = renderToStaticMarkup(
        createElement(conditions.default, {
          conditions: [rule],
          fields,
          period,
          scope,
          onChange,
        }),
      );
      assert.doesNotMatch(html, /kdj_j|KDJ J|54\.78/);
      if (snapshot.includes("kdj_j")) {
        assert.match(html, /保留原配置/);
        assert.match(html, /删除已隐藏的指标条件/);
        assert.doesNotMatch(html, /<select|type="number"/);
      } else {
        assert.match(html, /kdj_k/);
        assert.match(html, /kdj_d/);
        assert.match(
          html,
          scope === "exit" || scope === "trade-exit"
            ? /kd_dead_cross/
            : /kd_cross/,
        );
      }
      assert.equal(JSON.stringify(rule), snapshot);
    }
  }
const saved = {
  all: [
    {
      history_event: {
        window: { from: 30, to: 30 },
        match: "latest",
        where: { all: [{ field: "kdj_j", op: ">", value: 54.78 }] },
        then: { all: [{ field: "close", op: ">", right_event_field: "high" }] },
      },
    },
  ],
};
const snapshot = JSON.stringify(saved);
const html = renderToStaticMarkup(
  createElement(tree.default, {
    value: saved,
    onChange,
    fields,
    period: "W",
  }),
);
assert.doesNotMatch(html, /kdj_j|KDJ J|54\.78/);
assert.match(html, /保留原配置/);
assert.match(html, /观察日收盘价/);
assert.equal(JSON.stringify(saved), snapshot);
for (const window of [
  { from: 1, to: 30 },
  { from: 5, to: 20 },
  { from: 30, to: 30 },
]) {
  const value = {
    history_event: {
      window,
      match: "latest",
      where: {
        all: [{ field: "limit_up", op: "==", value: true, period: "D" }],
      },
      then: { all: [{ field: "change_pct", op: ">", value: 3, period: "D" }] },
    },
  };
  const before = JSON.stringify(value);
  const html = renderToStaticMarkup(
    createElement(tree.default, {
      value,
      onChange,
      fields: [
        ...fields,
        { field: "limit_up", type: "boolean", operators: ["=="] },
        { field: "change_pct", type: "number", operators: [">"] },
      ],
      period: "D",
    }),
  );
  assert.match(html, /近N个交易日内任一天/);
  assert.match(html, /自定义距今日范围/);
  assert.match(html, /指定前N个交易日/);
  assert.match(html, /不含今天/);
  assert.match(html, /今日条件/);
  assert.match(html, /收盘涨停事件/);
  assert.match(html, /事件日是否涨停/);
  assert.match(html, /今日涨幅 &gt; 3%/);
  assert.match(html, /MACD金叉/);
  assert.match(html, /KD金叉/);
  assert.equal(
    JSON.stringify(value),
    before,
    "opening each mode never rewrites the saved rule",
  );
}
for (const side of ["entry", "exit"])
  assert.doesNotMatch(trade.tradeRuleText(saved), /kdj_j|KDJ J|54\.78/);
const technicalFields = [
  "macd_cross",
  "macd_dif",
  "macd_dea",
  "kd_cross",
  "kdj_k",
  "kdj_d",
].map((field) => ({
  field,
  type: field.endsWith("cross") ? "boolean" : "number",
  operators: ["==", ">", "<"],
  supported_periods: ["D", "W", "M"],
  historical_event_supported: true,
}));
for (const scope of ["selection", "alert", "exit", "trade-entry", "trade-exit"])
  for (const period of ["D", "W", "M"]) {
    const value = { any: [{ field: "kd_cross", op: "==", value: true }] };
    const before = JSON.stringify(value);
    const html = renderToStaticMarkup(
      createElement(tree.default, {
        value,
        fields: technicalFields,
        period,
        scope,
        onChange,
      }),
    );
    assert.match(html, /MACD 水上金叉/);
    assert.match(html, /KDJ 低位金叉/);
    assert.match(html, /KDJ K &gt; D（持续状态）/);
    assert.match(html, /当期 DIF、DEA 都严格大于 0/);
    assert.match(html, /添加技术组合/);
    assert.equal((html.match(/aria-label="技术组合"/g) || []).length, 1);
    assert.equal(
      JSON.stringify(value),
      before,
      "opening a shortcut never changes an existing OR rule",
    );
  }
const technicalHistory = {
  all: [
    {
      history_event: {
        window: { from: 1, to: 30 },
        match: "latest",
        where: {
          all: [{ field: "kd_cross", op: "==", value: true, period: "D" }],
        },
        then: {
          all: [{ field: "macd_cross", op: "==", value: true, period: "D" }],
        },
      },
    },
  ],
};
const historyHtml = renderToStaticMarkup(
  createElement(tree.default, {
    value: technicalHistory,
    fields: technicalFields,
    period: "M",
    onChange,
  }),
);
assert.equal(
  (historyHtml.match(/aria-label="技术组合"/g) || []).length,
  3,
  "top-level and two history stages expose one toolbar each, not every nested group",
);
assert.equal(
  (historyHtml.match(/class="technical-preset-period">日K</g) || []).length,
  2,
);
assert.match(historyHtml, /class="technical-preset-period">完整月K</);
assert.doesNotMatch(
  renderToStaticMarkup(
    createElement(tree.default, {
      value: { all: [{ field: "close", op: ">", value: 1 }] },
      fields: [{ field: "close", type: "number", operators: [">"] }],
      period: "D",
      onChange,
    }),
  ),
  /aria-label="技术组合"/,
);
console.log(
  "condition visibility: real stock, history, alert, exit and trade editors omit hidden indicator across D/W/M, preserving saved rules and K/D cross conditions",
);
