import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
const dataUrl = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = (text, fileName = "test.tsx") =>
  ts.transpileModule(text, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const helpers = await import(
  dataUrl(
    compile(
      await fs.readFile(
        new URL("./hotBrowsePresentation.ts", import.meta.url),
        "utf8",
      ),
    ),
  )
);
const oldHot = await import(
  dataUrl(
    compile(
      (
        await fs.readFile(
          new URL("../features/strategies/HotUniverse.tsx", import.meta.url),
          "utf8",
        )
      ).replace('import "./HotUniverse.css";', ""),
    ).replaceAll(
      '"react/jsx-runtime"',
      JSON.stringify(import.meta.resolve("react/jsx-runtime")),
    ),
  )
);
const source = await fs.readFile(
  new URL("./HotBrowseFilter.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "HotBrowseFilter.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const functions = ast.statements.filter((node) =>
  ts.isFunctionDeclaration(node),
);
const body = compile(
  functions
    .map((node) =>
      node
        .getText(ast)
        .replace(/^export default /, "")
        .replace(/^export /, ""),
    )
    .join("\n"),
)
  .replace(/^import .+;\n/gm, "")
  .replace(/export \{\};?\n?/g, "");
let states = [],
  stateIndex = 0,
  paths = [];
const scope = {
  ...React,
  _jsx: jsx,
  _jsxs: jsxs,
  _Fragment: React.Fragment,
  ...helpers,
  defaultHotUniverse: oldHot.defaultHotUniverse,
  hotUniverseSummary: oldHot.hotUniverseSummary,
  validateHotUniverse: oldHot.validateHotUniverse,
  useState: (value) => [states[stateIndex++] ?? value, () => {}],
  useApi: (path) => {
    paths.push(path);
    return { data: null, loading: false, error: "", reload() {} };
  },
  Badge: ({ children, tone }) => jsx("span", { "data-tone": tone, children }),
  ErrorBox: ({ message }) => jsx("div", { role: "alert", children: message }),
  RefreshCw: () => null,
  X: () => null,
};
const module = new Function(
  ...Object.keys(scope),
  body + "\nreturn {HotBrowseFilter,HotBrowseBadge,useHotBrowseFilter};",
)(...Object.values(scope));
const currentScope = {
  business_date: "2026-10-23",
  snapshot_id: "generation-a",
  topic_revision_signature: "a".repeat(64),
};
const sourceRows = [
  { instrument_id: "600001.SH", entry_basis: { unchanged: true } },
];
const defaultModel = module.useHotBrowseFilter(sourceRows, currentScope);
assert.deepEqual(
  paths,
  [null],
  "default list browsing never asks for a hot ranking",
);
assert.equal(defaultModel.rows, sourceRows);
assert.equal(defaultModel.enabled, false);
const disabled = renderToStaticMarkup(
  jsx(module.HotBrowseFilter, { model: defaultModel, onRefreshList() {} }),
);
assert.ok(disabled.includes("未启用 · 显示全部范围"));
assert.ok(!disabled.includes("应用参数"));
states = [
  true,
  { ...oldHot.defaultHotUniverse },
  { ...oldHot.defaultHotUniverse },
  true,
];
stateIndex = 0;
paths = [];
const enabledModel = module.useHotBrowseFilter(sourceRows, currentScope);
assert.ok(paths[0].startsWith("/hot-universe?"));
assert.equal(enabledModel.rows[0]._hot_filter_state, "unknown");
assert.equal(enabledModel.includeUnknown, true);
assert.equal(defaultModel.includeUnknown, false);
assert.equal(
  defaultModel.mode,
  "observed",
  "enable defaults to genuinely observed memberships, not a full unknown list",
);
states = [
  true,
  { ...oldHot.defaultHotUniverse },
  { ...oldHot.defaultHotUniverse },
  false,
  "observed",
];
stateIndex = 0;
paths = [];
assert.equal(
  module.useHotBrowseFilter(sourceRows, currentScope, false).rows,
  sourceRows,
);
assert.deepEqual(
  paths,
  [null],
  "hidden watchlist alert view must not request stock hot ranking",
);

const config = { ...oldHot.defaultHotUniverse };
const actions = [];
const model = {
  ...enabledModel,
  applied: config,
  draft: { ...config, top_n: 5 },
  matched: 1,
  unmatched: 2,
  unknown: 3,
  observed: 2,
  observedUnknown: 1,
  ready: true,
  setMode: (value) => actions.push(["mode", value]),
  setEnabled: (value) => actions.push(["enabled", value]),
  setDraft: (value) => actions.push(["draft", value]),
  setApplied: (value) => actions.push(["applied", value]),
  setIncludeUnknown: (value) => actions.push(["unknown", value]),
  result: {
    data: {
      business_date: "2026-10-23",
      complete: false,
      window: { start: "2026-10-15", expected_trade_days: 7 },
      coverage: { source_complete_days: 6 },
      items: [{ topic_id: "t", rank: 1, name: "已观察题材", event_count: 3 }],
      reason: "缺少一天",
    },
    loading: false,
    error: "",
    reload: () => actions.push(["reload"]),
  },
};
const enabled = renderToStaticMarkup(
  jsx(module.HotBrowseFilter, { model, onRefreshList() {} }),
);
for (const label of [
  "当前仍按上方已应用范围",
  "近 7 个交易日",
  "前 10 名",
  "已完整核验匹配 1",
  "已排除 2",
  "已观察归属待核验 1",
  "其余归属待核验 2",
  "同时显示其余归属待核验",
  "当前有效成员代理",
  "6/7",
  "已观察题材",
  "缺少一天",
  "完整榜待核验",
])
  assert.ok(enabled.includes(label), label);
assert.ok(
  !enabled.includes("不随之后"),
  "browse receipt is current, not a permanently saved strategy-run receipt",
);
const tree = module.HotBrowseFilter({
  model,
  onRefreshList: () => actions.push(["refresh-list"]),
});
const flat = [];
function walk(node) {
  if (!node || typeof node !== "object") return;
  flat.push(node);
  for (const child of React.Children.toArray(node.props?.children)) walk(child);
}
walk(tree);
const button = (text) =>
  flat.find(
    (node) =>
      node.type === "button" &&
      React.Children.toArray(node.props.children).some(
        (child) => typeof child === "string" && child.includes(text),
      ),
  );
assert.equal(button("应用参数").props.disabled, false);
button("应用参数").props.onClick();
assert.deepEqual(actions.pop(), ["applied", { ...config, top_n: 5 }]);
button("刷新列表与热点").props.onClick();
assert.deepEqual(actions.splice(0), [["refresh-list"], ["reload"]]);
button("清空热点筛选").props.onClick();
assert.deepEqual(actions.splice(0), [
  ["enabled", false],
  ["draft", config],
  ["applied", config],
  ["unknown", false],
  ["mode", "observed"],
]);
const partial = renderToStaticMarkup(
  jsx(module.HotBrowseBadge, {
    row: { _hot_filter_state: "unknown", _hot_observed_match: true },
  }),
);
assert.ok(partial.includes("已观察热点归属 · 待核验"));
assert.equal(renderToStaticMarkup(jsx(module.HotBrowseBadge, { row: {} })), "");
console.log(
  "hot browse UI: opt-in request, current-proxy scope, incomplete coverage, explicit apply/refresh/clear, preserved unknowns and badges passed",
);
