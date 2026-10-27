import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";

const encoded = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, fileName = "helper.ts") =>
  ts
    .transpileModule(source, {
      fileName,
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
async function load(name) {
  const source = (
    await fs.readFile(new URL(name, import.meta.url), "utf8")
  ).replace(/^import "[^"\n]+\.css";\n/gm, "");
  return import(encoded(compile(source, name)));
}
const { strategyCopyDraft } = await load("./strategyCopy.ts");
const universeHelpers = await load("./Universe.tsx");
const ruleHelpers = await load("./historicalRules.ts");
const priceHelpers = await load("./strategyPriceBasis.ts");
const hotHelpers = await load("./HotUniverse.tsx");
const source = {
  strategy_id: "fixture-original",
  name: "倍量测试",
  version: 34,
  period: "W",
  price_basis: "forward",
  definition: {
    all: [
      { field: "board", op: "in", value: ["main", "chinext"] },
      { field: "is_st", op: "==", value: false },
      {
        field: "boll_signal",
        op: "matches",
        value: {
          ...ruleHelpers.BOLL_DEFAULTS,
          rank_window: 120,
          squeeze_percentile: 20,
          middle_direction: "rising",
          require_expansion: true,
        },
        period: "D",
      },
      {
        history_event: {
          window: { from: 1, to: 28 },
          match: "any",
          where: {
            all: [
              { field: "volume_ratio", op: ">=", value: 2, period: "D" },
              { field: "change_pct", op: ">", value: 2, period: "D" },
            ],
          },
          then: {
            field: "close",
            op: ">",
            right_event_field: "close",
            period: "D",
          },
        },
      },
    ],
  },
  hot_universe: { window_sessions: 9, top_n: 13, kind: "concept" },
  trade_rules: {
    entry: { field: "kd_cross", op: "==", value: true, period: "D" },
    exit: { field: "macd_dead_cross", op: "==", value: true, period: "W" },
  },
  policy: {
    strategy_id: "fixture-original",
    policy_version: 7,
    recorded_at_ms: 123,
    duration_sessions: 60,
    allow_entry_day_alerts: true,
    allow_entry_day_exits: false,
    alert_logic: "AND",
    exit_logic: "OR",
    alerts: [{ field: "tracking_change_pct", op: ">=", value: 5 }],
    exits: [{ field: "close_below_entry", op: "==", value: true }],
  },
  latest_run: { run_id: "original-run", matched_count: 20 },
  tracking_count: 35,
  automation: { enabled: true, auto_run: true, auto_track: true },
  deleted: false,
};
const snapshot = structuredClone(source);
const draft = strategyCopyDraft(source);
assert.equal(draft.name, "倍量测试 · 副本");
assert.deepEqual(
  Object.keys(draft).sort(),
  [
    "name",
    "period",
    "price_basis",
    "definition",
    "hot_universe",
    "trade_rules",
    "policy",
  ].sort(),
);
for (const field of [
  "period",
  "price_basis",
  "definition",
  "hot_universe",
  "trade_rules",
])
  assert.deepEqual(draft[field], source[field]);
assert.equal(draft.policy.duration_sessions, 60);
assert.equal(draft.policy.strategy_id, undefined);
assert.equal(draft.policy.policy_version, undefined);
const edited = strategyCopyDraft(source);
edited.definition.all[0].value.push("star");
edited.definition.all[2].value.deviations = 2.5;
edited.definition.all[3].history_event.window.to = 40;
edited.hot_universe.top_n = 5;
edited.trade_rules.entry.value = false;
edited.policy.alerts[0].value = 10;
edited.policy.exits.push({ field: "trading_age", op: ">", value: 30 });
assert.deepEqual(
  source,
  snapshot,
  "every nested configuration is detached from the loaded original",
);
assert.equal(
  strategyCopyDraft({ ...source, name: "长".repeat(100) }).name.length,
  100,
);
assert.equal(
  strategyCopyDraft({ ...source, price_basis: "none" }).price_basis,
  "none",
);

const pageSource = await fs.readFile(
  new URL("./Strategies.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Strategies.tsx",
  pageSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
let copyAction;
function visit(node) {
  if (
    ts.isJsxElement(node) &&
    node.openingElement.tagName.getText(ast) === "button"
  ) {
    const attributes = node.openingElement.attributes.properties;
    if (
      attributes.some(
        (attr) =>
          ts.isJsxAttribute(attr) &&
          attr.name.getText(ast) === "aria-label" &&
          attr.initializer?.getText(ast).includes("复制策略"),
      )
    )
      copyAction = attributes.find(
        (attr) =>
          ts.isJsxAttribute(attr) && attr.name.getText(ast) === "onClick",
      ).initializer.expression;
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(copyAction, "the selected strategy exposes the copy action");
const actionCode = compile("const click = " + copyAction.getText(ast)).replace(
  /export \{\};?\n?/g,
  "",
);
let opened;
new Function(
  "chosen",
  "setEditor",
  "strategyCopyDraft",
  `${actionCode};click();`,
)(
  source,
  (value) => {
    opened = value;
  },
  strategyCopyDraft,
);
assert.deepEqual(
  opened,
  draft,
  "the actual copy button opens the independent draft",
);
const editor = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "StrategyEditor",
);
const editorCode = compile(editor.getText(ast), "Editor.tsx")
  .replace(/^import .+;\n/gm, "")
  .replace(/export \{\};?\n?/g, "");
const children = (tree) => {
  if (!tree || typeof tree !== "object") return [];
  return [
    tree,
    ...React.Children.toArray(tree.props?.children).flatMap(children),
  ];
};
const text = (node) =>
  typeof node === "string"
    ? node
    : Array.isArray(node)
      ? node.map(text).join("")
      : node?.props
        ? text(node.props.children)
        : "";
async function editorCase(strategy, { save = true, modify = false } = {}) {
  let cursor = 0;
  const values = [],
    sent = [];
  const components = Object.fromEntries(
    [
      "Modal",
      "RuleTreeEditor",
      "UniverseEditor",
      "HotUniverseEditor",
      "TradeRulesEditor",
      "PolicyScope",
      "StrategyPriceBasisSelect",
      "ErrorBox",
    ].map((name) => [name, function Component() {}]),
  );
  const bindings = {
    _jsx: jsx,
    _jsxs: jsxs,
    ...components,
    ...universeHelpers,
    ...ruleHelpers,
    ...priceHelpers,
    validateHotUniverse: hotHelpers.validateHotUniverse,
    useState(initial) {
      const index = cursor++;
      if (!(index in values))
        values[index] = typeof initial === "function" ? initial() : initial;
      return [
        values[index],
        (next) => {
          values[index] =
            typeof next === "function" ? next(values[index]) : next;
        },
      ];
    },
    emptyTradeRules: () => ({ entry: null, exit: null }),
    // Scope validation has its own domain suites; payload tests need a stable
    // validator while retaining the actual rule normalization and save handler.
    validateVisualRule: () => null,
    useToast: () => () => {},
    post: async (path, body) => {
      sent.push({ method: "POST", path, body });
      return { strategy_id: "new-fixture", version: 1 };
    },
    patch: async (path, body) => {
      sent.push({ method: "PATCH", path, body });
      return {};
    },
  };
  const scope = new Proxy(bindings, {
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
  let saved = 0,
    closed = 0;
  const render = () => {
    cursor = 0;
    return Editor({
      strategy,
      fields: [],
      topics: [],
      onSaved() {
        saved++;
      },
      onClose() {
        closed++;
      },
    });
  };
  let tree = render();
  assert.equal(sent.length, 0, "opening a copy cannot write, execute or track");
  assert.equal(
    children(tree).some((node) => node.type === components.PolicyScope),
    !!strategy?.strategy_id,
  );
  if (modify) {
    children(tree)
      .find(
        (node) =>
          node.type === "input" && node.props.placeholder?.startsWith("例如"),
      )
      .props.onChange({ target: { value: "我调整的副本" } });
    children(tree)
      .find((node) => node.type === components.HotUniverseEditor)
      .props.onChange({ window_sessions: 11, top_n: 4, kind: "industry" });
    children(tree)
      .find((node) => node.type === "input" && node.props.max === 250)
      .props.onChange({ target: { value: "90" } });
    tree = render();
  }
  if (save) {
    await children(tree)
      .find((node) => node.type === "button" && text(node).includes("保存策略"))
      .props.onClick();
    assert.equal(sent.length, 1);
    assert.equal(saved, 1);
    assert.equal(closed, 1);
  } else {
    children(tree)
      .find((node) => node.type === "button" && text(node) === "取消")
      .props.onClick();
    assert.equal(sent.length, 0);
    assert.equal(saved, 0);
    assert.equal(closed, 1);
  }
  return sent[0];
}
await editorCase(strategyCopyDraft(source), { save: false });
const copied = await editorCase(strategyCopyDraft(source));
assert.equal(copied.method, "POST");
assert.equal(copied.path, "/strategies");
assert.equal(copied.body.apply_to_active_tracking, undefined);
assert.equal(copied.body.strategy_id, undefined);
assert.equal(copied.body.name, draft.name);
for (const field of [
  "period",
  "price_basis",
  "hot_universe",
  "trade_rules",
  "policy",
])
  assert.deepEqual(copied.body[field], draft[field]);
const split = universeHelpers.splitUniverse(source.definition);
assert.deepEqual(
  copied.body.definition,
  universeHelpers.withUniverse(
    ruleHelpers.normalizeRuleTree(split.definition),
    split.universe,
  ),
);
const adjusted = await editorCase(strategyCopyDraft(source), { modify: true });
assert.equal(adjusted.body.name, "我调整的副本");
assert.equal(adjusted.body.policy.duration_sessions, 90);
assert.deepEqual(adjusted.body.hot_universe, {
  window_sessions: 11,
  top_n: 4,
  kind: "industry",
});
assert.deepEqual(source, snapshot);
const editedOriginal = await editorCase(source);
assert.equal(editedOriginal.method, "PATCH");
assert.equal(editedOriginal.path, "/strategies/fixture-original");
assert.equal(editedOriginal.body.apply_to_active_tracking, true);
console.log(
  "strategy copy: deep detached full configuration, editable independent draft, cancel writes nothing, real editor POST vs original PATCH and no tracking/auto-execution passed",
);
