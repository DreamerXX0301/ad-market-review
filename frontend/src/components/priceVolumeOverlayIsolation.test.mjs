/** Exercise the production overlay actions without writing any saved chart state. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Charts.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const declarations = new Map();
function visit(node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name))
    declarations.set(node.name.text, node);
  ts.forEachChild(node, visit);
}
visit(ast);
const compile = (value) =>
  ts.transpileModule(value, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
const actions = ["toggleLine", "toggleAllLines"]
  .map((name) => {
    assert.ok(declarations.has(name));
    return `const ${declarations.get(name).getText(ast)};`;
  })
  .join("\n");
const automatic = ["stop", "support", "breakout"].map((kind) => ({
  id: `price-volume:${kind}`,
  group: "price-volume",
  enabled: false,
  price: kind === "stop" ? 8 : kind === "support" ? 9 : 12,
}));
const manual = {
  id: "saved-user-line",
  group: "annotation",
  enabled: false,
  price: 10,
};
const frozen = structuredClone([...automatic, manual]);
const state = { scope: "prior-chart", ids: new Set(["price-volume:stop"]) };
let priceState = state,
  hiddenState = { scope: "manual-scope", ids: new Set([manual.id]) };
const effects = [];
const create = (lines) =>
  new Function(
    "allLineLabels",
    "setPriceVolumeVisibility",
    "priceVolumeScope",
    "setHiddenLineState",
    "lineScope",
    "annotations",
    "update",
    "setShowHistoricalEvents",
    `${compile(actions)};return {toggleLine,toggleAllLines};`,
  )(
    lines,
    (value) => {
      priceState = typeof value === "function" ? value(priceState) : value;
    },
    "current-chart",
    (value) => {
      hiddenState = typeof value === "function" ? value(hiddenState) : value;
    },
    "manual-scope",
    { setShow: (value) => effects.push(["manual", value]) },
    (value) => effects.push(["preferences", value]),
    (value) => effects.push(["historical", value]),
  );

const mixed = create([...automatic, manual]);
const originalManualHidden = structuredClone(hiddenState);
mixed.toggleLine("price-volume:support", true);
assert.deepEqual(
  [...priceState.ids],
  ["price-volume:support"],
  "a new chart cannot reuse old automatic visibility",
);
mixed.toggleLine("price-volume:breakout", true);
mixed.toggleLine("price-volume:support", false);
assert.deepEqual([...priceState.ids], ["price-volume:breakout"]);
assert.deepEqual(
  hiddenState,
  originalManualHidden,
  "new overlays must not hide or reveal manual lines",
);
assert.deepEqual(
  effects,
  [],
  "new overlays must not enable history or persist existing preferences",
);
assert.deepEqual(
  [...automatic, manual],
  frozen,
  "display actions cannot rewrite any source level",
);

const automaticOnly = create(automatic);
automaticOnly.toggleAllLines(true);
assert.equal(priceState.ids.size, 3);
automaticOnly.toggleAllLines(false);
assert.equal(priceState.ids.size, 0);
assert.deepEqual(
  effects,
  [],
  "an automatic-only chart cannot change unrelated manual/history settings",
);

mixed.toggleLine(manual.id, true);
assert.deepEqual(
  effects,
  [["manual", true]],
  "the existing saved-line enable action still targets only its original layer",
);
assert.equal(hiddenState.ids.has(manual.id), false);
const visibleAutomatic = structuredClone(priceState);
mixed.toggleLine("not-a-line", true);
assert.deepEqual(priceState, visibleAutomatic);

const scope = declarations.get("priceVolumeScope");
assert.ok(scope?.initializer);
const chartScope = new Function(
  "instrumentId",
  "data",
  "asOf",
  "period",
  "dataEndpoint",
  `return (${scope.initializer.getText(ast)});`,
);
const data = {
  as_of: "2026-11-11",
  snapshot_id: "market-A",
  price_basis: "none",
};
const base = chartScope("000678.SZ", data, undefined, "D", undefined);
const switches = [
  chartScope("002300.SZ", data, undefined, "D", undefined),
  chartScope(
    "000678.SZ",
    { ...data, as_of: "2026-11-10" },
    undefined,
    "D",
    undefined,
  ),
  chartScope(
    "000678.SZ",
    { ...data, snapshot_id: "market-B" },
    undefined,
    "D",
    undefined,
  ),
  chartScope(
    "000678.SZ",
    { ...data, price_basis: "forward_event_affine" },
    undefined,
    "D",
    undefined,
  ),
  chartScope("000678.SZ", data, undefined, "W", undefined),
  chartScope("000678.SZ", data, undefined, "D", "/different-chart"),
];
assert.ok(switches.every((value) => value !== base));
console.log(
  "price-volume overlay isolation: exact chart scope, manual line/history/preference isolation, original manual controls and no source mutation passed",
);
