import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const code = ts.transpileModule(
  await fs.readFile(new URL("./policyRuleTree.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { policyRuleTree, policyGroupFromTree } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const event = {
  history_event: {
    window: { from: 30, to: 30 },
    match: "latest",
    where: { all: [{ field: "volume_ratio", op: ">=", value: 3 }] },
    then: { all: [{ field: "close", op: ">", right_event_field: "close" }] },
  },
};
const complex = [
  event,
  {
    any: [
      { field: "close_above_entry_high", op: "==", value: true },
      { field: "macd_cross", op: "==", value: true },
    ],
  },
];
for (const logic of ["AND", "OR"]) {
  const result = policyGroupFromTree(policyRuleTree(complex, logic));
  assert.equal(result.logic, logic);
  assert.strictEqual(result.conditions, complex);
  assert.deepEqual(result.conditions[0], event);
  assert.equal(result.conditions[1].any[0].field, "close_above_entry_high");
}
assert.deepEqual(policyGroupFromTree(policyRuleTree([], "OR")), {
  conditions: [],
  logic: "OR",
});
assert.deepEqual(
  policyGroupFromTree({
    any: [{ field: "close_below_entry_low", op: "==", value: true }],
  }),
  {
    conditions: [{ field: "close_below_entry_low", op: "==", value: true }],
    logic: "OR",
  },
);
assert.deepEqual(policyGroupFromTree(event), {
  conditions: [event],
  logic: "AND",
});
console.log(
  "policy tree roundtrip: independent historical bindings, nested groups, empty lists and tracking fields preserved",
);
