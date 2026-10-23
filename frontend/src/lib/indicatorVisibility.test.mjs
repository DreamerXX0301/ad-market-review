import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./indicatorVisibility.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  HIDDEN_INDICATOR_CONDITION_TEXT,
  isHiddenIndicatorField,
  visibleIndicatorData,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

for (const field of ["kdj_j", "kdj_j_reason", "display_kdj_j", "KDJ J"])
  assert.equal(isHiddenIndicatorField(field), true, field);
for (const field of ["kdj_k", "kdj_d", "kd_cross", "kd_dead_cross", "J", null])
  assert.equal(isHiddenIndicatorField(field), false, String(field));

const original = {
  observations: { kdj_j: 93.2, kdj_k: 0, kdj_d: null, macd_hist: -1 },
  metrics_by_period: {
    W: { kdj_j: null, kdj_j_reason: "未完成预热", kdj_k: null, kdj_d: 0 },
  },
  affected_fields: ["kdj_k", "kdj_d", "kdj_j"],
  checks: [
    { field: "kdj_j", value: 80, actual: 93.2, result: true },
    { field: "kdj_k", right_field: "kdj_j", actual: 0, right_actual: 93.2 },
    { field: "close", right_event_field: "kdj_j", actual: 10 },
    { field: "kd_cross", value: true, actual: null, result: null },
    { field: "kdj_k", value: 0, actual: 0, result: true },
  ],
};
const before = JSON.stringify(original);
const shown = visibleIndicatorData(original);
assert.deepEqual(shown.observations, {
  kdj_k: 0,
  kdj_d: null,
  macd_hist: -1,
});
assert.deepEqual(shown.metrics_by_period.W, { kdj_k: null, kdj_d: 0 });
assert.deepEqual(shown.affected_fields, ["kdj_k", "kdj_d"]);
for (const check of shown.checks.slice(0, 3))
  assert.deepEqual(check, { description: HIDDEN_INDICATOR_CONDITION_TEXT });
assert.deepEqual(shown.checks.slice(3), original.checks.slice(3));
assert.equal(JSON.stringify(original), before, "source data stays unchanged");
assert.notEqual(shown.observations, original.observations);
assert.equal(JSON.stringify(shown).includes("93.2"), false);
assert.equal(JSON.stringify(shown).includes("kdj_j"), false);
assert.equal(visibleIndicatorData(null), null);
assert.equal(visibleIndicatorData(0), 0);
assert.equal(visibleIndicatorData(false), false);
assert.equal(
  visibleIndicatorData("K 56.42\nD 57.24\nJ 54.78"),
  "K 56.42\nD 57.24\n部分指标内容已隐藏",
);
assert.equal(visibleIndicatorData("KDJ 9 · 3 · 3"), "KDJ 9 · 3 · 3");
assert.equal(visibleIndicatorData("观察值 kdj_j: 54.78"), "部分指标内容已隐藏");
console.log("indicator visibility: passed");
