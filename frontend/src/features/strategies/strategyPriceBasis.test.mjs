import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, fileName = "helper.ts") =>
  ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const helperUrl = encode(
  compile(
    await fs.readFile(
      new URL("./strategyPriceBasis.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const helpers = await import(helperUrl);
const {
  initialStrategyPriceBasis: initial,
  strategyRunPriceBasis: runBasis,
  candidatePriceBasis: actual,
} = helpers;
assert.equal(initial(), "forward");
assert.equal(initial({}), "none");
assert.equal(initial({ price_basis: "none" }), "none");
assert.equal(initial({ price_basis: "forward" }), "forward");
assert.equal(runBasis({ requested_price_basis: "forward" }), "本地前复权");
assert.equal(runBasis({ requested_price_basis: "none" }), "未复权");
assert.match(runBasis({}), /未复权.*历史版本/);
assert.match(runBasis(null), /待核验/);
assert.match(runBasis({ requested_price_basis: "bad" }), /待核验/);
const forwardRun = { requested_price_basis: "forward", strategy_version: 2 };
const oldRun = { requested_price_basis: "none", strategy_version: 1 };
assert.match(
  actual(
    {
      metrics: {
        price_basis: "forward_event_affine",
        anchor_date: "2026-10-23",
      },
    },
    forwardRun,
  ).reason,
  /锚定 2026-10-23/,
);
assert.equal(
  actual(
    {
      metrics: {
        price_basis: "forward_event_affine",
        adjustment: { anchor: "2026-10-23" },
      },
    },
    forwardRun,
  ).label,
  "本地前复权",
);
assert.match(
  actual(
    {
      metrics: {
        price_basis: "forward_event_affine",
        adjustment: { anchor: "2026-10-23" },
      },
    },
    forwardRun,
  ).reason,
  /2026-10-23/,
);
assert.equal(
  actual({ metrics: { price_basis: "none" } }, forwardRun).label,
  "未复权",
);
assert.equal(
  actual(
    {
      metrics: {
        price_basis: "unavailable",
        adjustment: { reason: "公司行动重复" },
      },
    },
    forwardRun,
  ).reason,
  "公司行动重复",
);
assert.equal(
  actual({ metrics: { price_basis: "unavailable" } }, forwardRun).unavailable,
  true,
);
assert.match(
  actual({}, forwardRun).label,
  /待核验/,
  "a run requesting forward prices does not prove that every stock succeeded",
);
assert.match(actual({}, oldRun).label, /未复权.*历史结果/);
assert.match(actual({}, {}).label, /未复权.*历史结果/);
assert.match(
  actual({ metrics: { price_basis: "unknown" } }, oldRun).label,
  /待核验/,
);

let uiSource = compile(
  await fs.readFile(
    new URL("./StrategyPriceBasisControls.tsx", import.meta.url),
    "utf8",
  ),
  "StrategyPriceBasis.tsx",
)
  .replace('from "./strategyPriceBasis"', `from ${JSON.stringify(helperUrl)}`)
  .replace(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  );
const ui = await import(encode(uiSource));
for (const value of ["forward", "none"]) {
  const html = renderToStaticMarkup(
    jsx(ui.StrategyPriceBasisSelect, {
      value,
      onChange: () => assert.fail("render must not save a strategy"),
    }),
  );
  assert.match(html, /技术指标价格口径/);
  assert.match(html, new RegExp(`<option value="${value}" selected="">`));
  assert.match(html, /已保存结果保持原口径/);
}
const unavailable = renderToStaticMarkup(
  jsx(ui.CandidatePriceBasis, {
    row: {
      metrics: {
        price_basis: "unavailable",
        adjustment: { reason: "缺少证据 <pending>" },
      },
    },
    run: forwardRun,
  }),
);
assert.match(unavailable, /复权待核验/);
assert.match(unavailable, /&lt;pending&gt;/);

// Exercise the real editor's create/edit save path in an isolated hook/transport
// environment, so default labels and the actual POST/PATCH payload stay aligned.
const source = await fs.readFile(
  new URL("./Strategies.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Strategies.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const editor = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "StrategyEditor",
);
const editorCode = compile(editor.getText(ast), "Editor.tsx")
  .replace(/^import .+;\n/gm, "")
  .replace(/export \{\};?\n?/g, "");
const find = (node, predicate) => {
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return node;
  for (const child of React.Children.toArray(node.props?.children)) {
    const result = find(child, predicate);
    if (result) return result;
  }
  return null;
};
const text = (node) =>
  typeof node === "string"
    ? node
    : Array.isArray(node)
      ? node.map(text).join("")
      : node?.props
        ? text(node.props.children)
        : "";
async function editorCase(strategy, choose) {
  const values = [];
  let cursor = 0;
  const sent = [];
  const original = strategy ? structuredClone(strategy) : null;
  const scopeValues = {
    _jsx: jsx,
    _jsxs: jsxs,
    ...helpers,
    ...ui,
    useState: (initialValue) => {
      const index = cursor++;
      if (!(index in values))
        values[index] =
          typeof initialValue === "function" ? initialValue() : initialValue;
      return [
        values[index],
        (value) => {
          values[index] = value;
        },
      ];
    },
    splitUniverse: (definition) => ({
      universe: { mode: "all", st: "all" },
      definition,
    }),
    emptyTradeRules: () => ({ entry: null, exit: null }),
    normalizeRuleTree: (value) => value,
    validateVisualRule: () => null,
    validateHotUniverse: () => null,
    hasHiddenConditionRule: () => false,
    withUniverse: (value) => value,
    ruleCount: () => 1,
    useToast: () => () => {},
    post: async (path, body) => {
      sent.push({ method: "POST", path, body });
      return {};
    },
    patch: async (path, body) => {
      sent.push({ method: "PATCH", path, body });
      return {};
    },
  };
  const scope = new Proxy(scopeValues, {
    has: () => true,
    get: (target, key) =>
      key === Symbol.unscopables
        ? undefined
        : key in target
          ? target[key]
          : key in globalThis
            ? globalThis[key]
            : () => null,
  });
  const Editor = new Function(
    "scope",
    `with(scope){${editorCode};return StrategyEditor;}`,
  )(scope);
  const render = () => {
    cursor = 0;
    return Editor({
      strategy,
      fields: [],
      topics: [],
      onSaved() {},
      onClose() {},
    });
  };
  let tree = render();
  assert.equal(
    sent.length,
    0,
    "opening an editor never runs or saves a strategy",
  );
  const selector = find(
    tree,
    (node) => node.type === ui.StrategyPriceBasisSelect,
  );
  assert.equal(
    selector.props.value,
    strategy ? strategy.price_basis || "none" : "forward",
  );
  find(
    tree,
    (node) =>
      node.type === "input" && node.props.placeholder?.startsWith("例如"),
  ).props.onChange({ target: { value: "测试策略" } });
  if (choose) selector.props.onChange(choose);
  tree = render();
  await find(
    tree,
    (node) => node.type === "button" && text(node).includes("保存策略"),
  ).props.onClick();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].method, strategy ? "PATCH" : "POST");
  assert.equal(
    sent[0].body.price_basis,
    choose || (strategy ? strategy.price_basis || "none" : "forward"),
  );
  if (strategy)
    assert.deepEqual(
      strategy,
      original,
      "saving a new version cannot mutate the loaded old version",
    );
}
await editorCase();
await editorCase({
  strategy_id: "old",
  name: "旧策略",
  definition: { field: "close", op: ">", value: 0 },
});
await editorCase(
  {
    strategy_id: "old",
    name: "旧策略",
    definition: { field: "close", op: ">", value: 0 },
  },
  "forward",
);
await editorCase(
  {
    strategy_id: "forward",
    name: "前复权策略",
    price_basis: "forward",
    definition: { field: "close", op: ">", value: 0 },
  },
  "none",
);
assert.match(source, /strategyRunPriceBasis\(run\)/);
assert.doesNotMatch(source, /strategyRunPriceBasis\(chosen/);
assert.match(source, /<CandidatePriceBasis row=\{row\} run=\{run\}/);
let bootstrapNode;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "bootstrap")
    bootstrapNode = node;
  ts.forEachChild(node, visit);
}
visit(ast);
const bootstrapCode = compile(bootstrapNode.getText(ast)).replace(
  /export \{\};?\n?/g,
  "",
);
const bootstrapCalls = [];
const bootstrap = new Function(
  "post",
  "setBusy",
  "result",
  "topicsResult",
  "toast",
  "setError",
  `${bootstrapCode};return bootstrap;`,
)(
  async (path, body) => bootstrapCalls.push({ path, body }),
  () => {},
  { reload() {} },
  { reload() {} },
  () => {},
  (error) => assert.fail(error),
);
await bootstrap();
assert.deepEqual(bootstrapCalls, [
  { path: "/strategies/bootstrap", body: { price_basis: "forward" } },
]);
console.log(
  "Strategy price basis: real new/edit save payloads, legacy preservation, frozen run labels and per-stock unavailable evidence passed",
);
