import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./conditionScope.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { conditionFieldGroups, trackingConditionAllowed } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const fields = [
  { field: "macd_cross", type: "boolean" },
  { field: "kd_cross", type: "boolean" },
  { field: "macd_dead_cross", type: "boolean" },
  { field: "kd_dead_cross", type: "boolean" },
  { field: "close_below_entry_low", type: "boolean" },
  { field: "cross_above_ma20", type: "boolean" },
  { field: "close", type: "number" },
];
assert.equal(trackingConditionAllowed("close_below_entry_low"), false);
assert.equal(trackingConditionAllowed("close_below_entry_low", true), false);
assert.equal(
  trackingConditionAllowed("close_below_entry_low", false, true),
  true,
);
assert.equal(
  trackingConditionAllowed("close_above_entry_high", false, true),
  false,
);
assert.equal(
  trackingConditionAllowed("close_above_entry_high", true, true),
  true,
);
assert.equal(trackingConditionAllowed("macd_dead_cross"), true);
const allFields = (groups) =>
  groups.flatMap((group) => group.fields.map((field) => field.field));
for (const scope of ["alert", "trade-entry"]) {
  const groups = conditionFieldGroups(fields, scope, [], false);
  assert.deepEqual(
    groups[0].fields.map((field) => field.field),
    ["macd_cross", "kd_cross", "cross_above_ma20"],
  );
  assert(!allFields(groups).includes("macd_dead_cross"));
  assert(allFields(groups).includes("close"));
}
for (const scope of ["exit", "trade-exit"]) {
  const groups = conditionFieldGroups(fields, scope, [], false);
  assert.deepEqual(
    groups[0].fields.map((field) => field.field),
    ["macd_dead_cross", "kd_dead_cross", "close_below_entry_low"],
  );
  assert(!allFields(groups).includes("macd_cross"));
  assert(allFields(groups).includes("close"));
  const preserved = conditionFieldGroups(fields, scope, ["macd_cross"], false);
  assert(
    preserved
      .find((group) => group.label === "已保存 · 跨场景条件")
      .fields.some((field) => field.field === "macd_cross"),
  );
  assert.deepEqual(
    new Set(allFields(conditionFieldGroups(fields, scope, [], true))),
    new Set(fields.map((field) => field.field)),
  );
}
assert.deepEqual(
  new Set(allFields(conditionFieldGroups(fields, "selection", [], false))),
  new Set(fields.map((field) => field.field)),
);

// Check the actual TSX evidence formatter without loading React or mounting UI.
const conditionsSource = await fs.readFile(
  new URL("./Conditions.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Conditions.tsx",
  conditionsSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const formatter = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) &&
    node.name?.text === "entryLowEvidenceText",
);
assert(formatter);
const formatterCode = ts.transpileModule(
  "const num = (value) => value.toFixed(2);\n" + formatter.getText(ast),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { entryLowEvidenceText } = await import(
  "data:text/javascript;base64," + Buffer.from(formatterCode).toString("base64")
);
assert.equal(
  entryLowEvidenceText({
    field: "close_below_entry_low",
    actual: false,
    evidence: { entry_low: { state: "ready", raw_low: 9, date: "2026-10-01" } },
  }),
  "实际否；事件日 2026-10-01 最低价 9.00",
);
assert.equal(
  entryLowEvidenceText({ field: "close_below_entry_low", actual: null }),
  "实际待核验；事件日最低价待核验",
);
assert.equal(
  entryLowEvidenceText({
    field: "close_below_entry_low",
    actual: true,
    evidence: { entry_low: { state: "unknown", raw_low: 9 } },
  }),
  "实际是；事件日最低价待核验",
);
assert.equal(
  entryLowEvidenceText({ field: "close_below_entry", actual: true }),
  null,
);
console.log(
  "condition scopes: distinct golden/dead cross recommendations, shared numbers, preserved legacy fields and low-price evidence passed",
);
