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
  for (const [id, field] of [["volume-expand", "volume_expand"], ["volume-shrink", "volume_shrink"]]) {
    assert.deepEqual(createTechnicalPreset(id, period), { field, op: "==", value: true, period });
    assert.deepEqual(availableTechnicalPresets([{ field, operators: ["=="], hidden_in_builder: true, supported_periods: [period] }], period).map(p => p.id), [id]);
  }
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
assert.equal(availableTechnicalPresets(fields, "D").length, 4);
assert.deepEqual(
  availableTechnicalPresets(
    fields.filter((field) => field.field !== "macd_dea"),
    "D",
  ).map((p) => p.id),
  ["kd-low-cross", "kd-above", "kd-below"],
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

// Sell signals stay events; K<D is a separate persistent state.
for (const period of ["D", "W", "M"]) {
  assert.deepEqual(createTechnicalPreset("macd-dead-cross", period), {
    field: "macd_dead_cross", op: "==", value: true, period,
  });
  assert.deepEqual(createTechnicalPreset("kd-dead-cross", period), {
    field: "kd_dead_cross", op: "==", value: true, period,
  });
  assert.deepEqual(createTechnicalPreset("kd-below", period), {
    field: "kdj_k", op: "<", right_field: "kdj_d", period,
  });
  const water = createTechnicalPreset("macd-above-zero-dead", period);
  assert.deepEqual(water.all, [
    {field: "macd_dead_cross", op: "==", value: true, period},
    {field: "macd_dif", op: ">", value: 0, period},
    {field: "macd_dea", op: ">", value: 0, period},
  ]);
  const high = createTechnicalPreset("kd-high-dead", period);
  assert.deepEqual(high.all, [
    {field: "kd_dead_cross", op: "==", value: true, period},
    {field: "kdj_k", op: ">", value: 80, period},
    {field: "kdj_d", op: ">", value: 80, period},
  ]);
  for (const threshold of [0, 60, 82.5, 100]) {
    const custom = createTechnicalPreset("kd-high-dead", period, threshold);
    assert.equal(custom.all[1].value, threshold);
    assert.equal(custom.all[2].value, threshold);
    assert.equal(custom.all[1].op, ">");
  }
  assert.deepEqual(appendTechnicalPreset(oldOr, water).any, [oldLeaf, water], "water death must remain an atomic AND inside OR");
}
for (const invalid of [-1, 101, NaN, Infinity, -Infinity, "80", null])
  assert.throws(() => createTechnicalPreset("kd-high-dead", "D", invalid));
const sellFields = [...fields, ...["macd_dead_cross", "kd_dead_cross"].map(field => ({
  field, operators: ["=="], supported_periods: ["D", "W", "M"],
}))];
assert.deepEqual(availableTechnicalPresets(sellFields, "D", "trade-exit").slice(0, 5).map(p => p.id), [
  "macd-dead-cross", "kd-dead-cross", "macd-above-zero-dead", "kd-high-dead", "kd-below",
]);
assert.equal(availableTechnicalPresets(sellFields, "D", "trade-entry")[0].id, "macd-above-zero");
assert(!availableTechnicalPresets(fields, "D", "trade-exit").some(p => p.id === "macd-dead-cross"));
assert.equal(JSON.stringify(oldOr), before, "adding sell templates must not modify the saved tree");
console.log("sell presets: MACD/KD events, water/high gates, editable threshold, state distinction, scope priority and atomic grouping passed");
