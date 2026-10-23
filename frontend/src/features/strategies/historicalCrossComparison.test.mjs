import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
async function load(name) {
  const source = ts.transpileModule(
    await fs.readFile(new URL(name, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
  return import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
  );
}
const rules = await load("./historicalRules.ts");
const technical = await load("./technicalConditionPresets.ts");
const {
  newHistoryRule,
  newPreviousCrossRule,
  applyPreviousCrossPreset,
  appendTodayCrossAlternative,
  pureGoldenCross,
  previousCrossConfigurationError,
  conditionComparisonObject,
  withConditionComparison,
  eventPriceOffsetError,
  eventPriceTargetText,
  historyPresetWhere,
  historyEventPreset,
  historyRuleSummary,
  validateVisualRule,
  normalizeRuleTree,
} = rules;

const old = { field: "close", op: "<", right_event_field: "close" };
const before = JSON.stringify(old);
assert.equal(conditionComparisonObject(old), "event");
assert.equal(eventPriceTargetText(old), "事件日收盘价");
assert.deepEqual(normalizeRuleTree(old), old);
assert.equal(Object.hasOwn(old, "right_event_change_pct"), false);
const percentage = withConditionComparison(old, "event-percentage");
assert.equal(conditionComparisonObject(percentage), "event-percentage");
assert.equal(percentage.right_event_change_pct, 0);
assert.equal(percentage.period, "D");
assert.equal(JSON.stringify(old), before);
for (const pct of [-99.999, -3, 0, 3, 10000]) {
  assert.equal(eventPriceOffsetError(pct), null);
  const node = { ...percentage, right_event_change_pct: pct };
  const tree = newHistoryRule();
  tree.history_event.then = { all: [node] };
  assert.equal(validateVisualRule(tree), null);
  assert.deepEqual(normalizeRuleTree(tree), tree);
  for (const kind of ["value", "field", "event"]) {
    const switched = withConditionComparison(node, kind);
    assert.equal(Object.hasOwn(switched, "right_event_change_pct"), false);
    assert.equal(conditionComparisonObject(switched), kind);
  }
}
for (const pct of [-100, -101, 10000.001, "", "3", true, null, Infinity, NaN])
  assert.equal(typeof eventPriceOffsetError(pct), "string");
assert.equal(
  eventPriceTargetText({ ...percentage, right_event_change_pct: 3 }),
  "事件日收盘价 × (1 + 3/100)",
);
assert.equal(
  eventPriceTargetText({ ...percentage, right_event_change_pct: -3 }),
  "事件日收盘价 × (1 − 3/100)",
);
assert.match(
  validateVisualRule({
    field: "close",
    op: "<",
    value: 3,
    right_event_change_pct: 3,
  }),
  /仅用于/,
);
const band = newHistoryRule();
band.history_event.then = {
  all: [
    { ...percentage, op: ">=", right_event_change_pct: -3 },
    { ...percentage, op: "<=", right_event_change_pct: 3 },
  ],
};
assert.equal(validateVisualRule(band), null);
assert.match(historyRuleSummary(band), /1 − 3\/100/);
assert.match(historyRuleSummary(band), /1 \+ 3\/100/);

const or = {
  any: [
    { field: "kd_cross", op: "==", value: true, period: "D" },
    { field: "change_pct", op: ">", value: 5, period: "D" },
  ],
};
const base = { ...newHistoryRule().history_event, then: or };
for (const indicator of ["macd", "kdj"]) {
  const fresh = newPreviousCrossRule(indicator);
  const event = fresh.history_event;
  assert.deepEqual(event.window, { from: 1, to: 60 });
  assert.equal(event.match, "latest");
  assert.equal(pureGoldenCross(event.where), indicator);
  assert.deepEqual(event.price_path.cross_cycle, {
    indicator,
    require_dead_cross: true,
  });
  assert.equal(validateVisualRule(fresh), null);
  assert.equal(
    JSON.stringify(event.then).includes("change_pct"),
    false,
    "new cross template cannot silently require an up day",
  );
  assert.equal(event.then.all.length, 2);
  assert.equal(event.then.all[1].right_event_field, "close");
  const applied = applyPreviousCrossPreset(base, indicator);
  assert.equal(applied.then.all[0], or);
  assert.deepEqual(
    applyPreviousCrossPreset(applied, indicator),
    applied,
    "reapplying the same cross template is idempotent",
  );
  for (const to of [30, 60, 750]) {
    const edited = {
      ...event,
      window: { from: 1, to },
      then: {
        all: [
          ...event.then.all,
          { field: "kdj_k", op: ">=", value: 30, period: "D" },
          { field: "kdj_k", op: "<=", value: 80, period: "D" },
          { ...percentage, right_event_change_pct: 3 },
        ],
      },
    };
    assert.equal(previousCrossConfigurationError(edited), null);
    assert.equal(
      pureGoldenCross(edited.where),
      indicator,
      "today K/price changes cannot filter the historical event",
    );
    assert.equal(validateVisualRule({ history_event: edited }), null);
  }
  const invalid = [
    { ...event, match: "earliest" },
    { ...event, window: { from: 2, to: 60 } },
    { ...event, where: historyPresetWhere("volume") },
    {
      ...event,
      where: {
        all: [
          ...event.where.all,
          { field: "kdj_k", op: ">", value: 30, period: "D" },
        ],
      },
    },
    { ...event, where: { ...event.where.all[0], period: "W" } },
    { ...event, where: { ...event.where, extra: true } },
  ];
  for (const bad of invalid)
    assert.match(validateVisualRule({ history_event: bad }), /上次金叉过程/);
}
assert.equal(base.then, or);
const anyCross = appendTodayCrossAlternative(base);
assert.equal(anyCross.then.all[0], or);
assert.equal(anyCross.then.all[1].any.length, 2);
assert(anyCross.then.all[1].any.every((leaf) => leaf.period === "D"));
assert.deepEqual(appendTodayCrossAlternative(anyCross), anyCross);
const signals = historyPresetWhere("any-signal");
assert.deepEqual(
  signals.any.map((leaf) => leaf.field),
  ["volume_ratio", "limit_up", "change_pct"],
);
assert(signals.any.every((leaf) => leaf.period === "D"));
assert.equal(signals.any[2].op, ">");
assert.equal(signals.any[2].value, 5);
assert.equal(historyEventPreset(signals), "any-signal");
assert.equal(historyEventPreset(historyPresetWhere("change")), "change");

for (const period of ["D", "W", "M"])
  for (const id of ["kd-cross-range", "kd-range"])
    for (const [min, max] of [
      [0, 20],
      [20, 80],
      [80, 100],
      [0, 100],
      [0, 0],
      [20, 20],
      [100, 100],
      [30.5, 79.25],
    ]) {
      const node = technical.createTechnicalPreset(id, period, 20, {
        min,
        max,
      });
      const numeric = node.all.filter((leaf) => leaf.field !== "kd_cross");
      assert.deepEqual(numeric, [
        { field: "kdj_k", op: ">=", value: min, period },
        { field: "kdj_k", op: "<=", value: max, period },
      ]);
      assert(
        !JSON.stringify(node).includes("kdj_d"),
        "K range must never constrain D",
      );
      assert.equal(
        node.all.some((leaf) => leaf.field === "kd_cross"),
        id === "kd-cross-range",
      );
      assert.equal(validateVisualRule(node), null);
    }
for (const pair of [
  [-1, 20],
  [20, 101],
  [80, 20],
  ["", 20],
  [20, "80"],
  [NaN, 80],
  [0, Infinity],
])
  assert.throws(() =>
    technical.createTechnicalPreset("kd-cross-range", "D", 20, {
      min: pair[0],
      max: pair[1],
    }),
  );
const completeFields = ["kd_cross", "kdj_k"].map((field) => ({
  field,
  operators: ["==", ">=", "<="],
  supported_periods: ["D"],
}));
assert.deepEqual(
  technical
    .availableTechnicalPresets(completeFields, "D")
    .map((item) => item.id),
  ["kd-cross-range", "kd-range"],
);
assert(
  technical.ruleReplacementError(
    { all: Array(96).fill({ field: "close", op: ">", value: 1 }) },
    { field: "close", op: ">", value: 1 },
    newPreviousCrossRule("macd"),
    1,
  ),
);
console.log(
  "historical cross comparison: signed event offsets, pure previous-cross templates, no implicit gain, preserved OR, strict cycle configuration, K-only inclusive ranges and budgets passed",
);
