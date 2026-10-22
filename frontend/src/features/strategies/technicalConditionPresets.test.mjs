import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const compiled = ts.transpileModule(
  await fs.readFile(
    new URL("./technicalConditionPresets.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  createTechnicalPreset,
  appendTechnicalPreset,
  availableTechnicalPresets,
  ruleTreeSize,
  technicalPresetInsertionError,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

for (const period of ["D", "W", "M"]) {
  assert.deepEqual(createTechnicalPreset("macd-above-zero", period), {
    all: [
      { field: "macd_cross", op: "==", value: true, period },
      { field: "macd_dif", op: ">", value: 0, period },
      { field: "macd_dea", op: ">", value: 0, period },
    ],
  });
  for (const upper of [0, 20, 30.5, 100]) {
    const low = createTechnicalPreset("kd-low-cross", period, upper);
    assert.equal(low.all[0].field, "kd_cross");
    assert.equal(low.all[0].value, true);
    assert.deepEqual(low.all.slice(1), [
      { field: "kdj_k", op: "<", value: upper, period },
      { field: "kdj_d", op: "<", value: upper, period },
    ]);
    assert(low.all.every((node) => node.period === period));
    assert(!("period" in low), "the composite itself must never carry period");
  }
  assert.deepEqual(createTechnicalPreset("kd-above", period), {
    field: "kdj_k",
    op: ">",
    right_field: "kdj_d",
    period,
  });
}
for (const upper of [-1, 101, NaN, Infinity, -Infinity, "20", null])
  assert.throws(() => createTechnicalPreset("kd-low-cross", "D", upper));
assert.throws(() => createTechnicalPreset("macd-above-zero", "Q"));
assert.throws(() => createTechnicalPreset("unsupported", "D"));

const oldLeaf = Object.freeze({
  field: "change_pct",
  op: ">",
  value: 2,
  period: "D",
});
const oldOr = Object.freeze({ any: Object.freeze([oldLeaf]) });
const before = JSON.stringify(oldOr);
const preset = createTechnicalPreset("macd-above-zero", "M");
const nextOr = appendTechnicalPreset(oldOr, preset);
assert.deepEqual(nextOr.any, [oldLeaf, preset]);
assert.equal(
  nextOr.any[1].all.length,
  3,
  "all three water-cross checks remain one OR alternative",
);
assert.equal(JSON.stringify(oldOr), before);
assert.deepEqual(appendTechnicalPreset(oldLeaf, preset), {
  all: [oldLeaf, preset],
});
assert.deepEqual(appendTechnicalPreset({ all: [] }, preset), { all: [preset] });
assert.notEqual(
  createTechnicalPreset("kd-low-cross", "D").all,
  createTechnicalPreset("kd-low-cross", "D").all,
);

const fields = [
  "macd_cross",
  "macd_dif",
  "macd_dea",
  "kd_cross",
  "kdj_k",
  "kdj_d",
].map((field) => ({
  field,
  operators: ["==", ">", "<"],
  supported_periods: ["D", "W", "M"],
}));
assert.equal(availableTechnicalPresets(fields, "D").length, 3);
assert.deepEqual(
  availableTechnicalPresets(
    fields.filter((field) => field.field !== "macd_dea"),
    "D",
  ).map((p) => p.id),
  ["kd-low-cross", "kd-above"],
);
assert.deepEqual(availableTechnicalPresets([], "D"), []);
assert.equal(
  availableTechnicalPresets(
    fields.map((field) => ({ ...field, supported_periods: ["D"] })),
    "M",
  ).length,
  0,
);
assert.equal(
  availableTechnicalPresets(
    fields.map((field) => ({ ...field, operators: [">"] })),
    "D",
  ).length,
  1,
);

const history = {
  all: [
    {
      history_event: {
        where: { all: [oldLeaf] },
        then: { all: [oldLeaf] },
      },
    },
  ],
};
assert.deepEqual(ruleTreeSize(history), { count: 6, depth: 3 });
assert.equal(
  technicalPresetInsertionError(
    history,
    history.all[0].history_event.then,
    preset,
    2,
  ),
  null,
);
assert.match(
  technicalPresetInsertionError(
    { all: Array(96).fill(oldLeaf) },
    { all: [] },
    preset,
    0,
  ),
  /100节点/,
);
assert.match(
  technicalPresetInsertionError(
    { all: [] },
    { any: Array(50).fill(oldLeaf) },
    preset,
    0,
  ),
  /50项/,
);
assert.match(
  technicalPresetInsertionError(
    history,
    history.all[0].history_event.then,
    preset,
    5,
  ),
  /嵌套过深/,
);
assert.equal(
  technicalPresetInsertionError(
    { all: Array(95).fill(oldLeaf) },
    { all: [] },
    preset,
    0,
  ),
  null,
);
console.log(
  "technical presets: same-period water/low cross, state distinction, atomic OR insertion, editable thresholds, dependency and tree-budget checks passed",
);
