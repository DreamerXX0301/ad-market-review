import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const compiled = ts.transpileModule(
  await fs.readFile(new URL("./historicalRules.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const rules = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const {
  newHistoryRule,
  normalizeRuleTree,
  validateVisualRule,
  historyRuleSummary,
  historyCheckText,
  defaultHistoryGuard,
  defaultHistoryReclaim,
  withHistoryPricePart,
  applyHistoryPricePreset,
  validateHistoryPricePath,
  historyPricePathSummary,
  historyReclaimModes,
} = rules;

const old = newHistoryRule();
delete old.history_event.then.all[0].period;
const frozenOld = JSON.stringify(old);
assert.deepEqual(normalizeRuleTree(old), old);
assert.equal(validateVisualRule(old), null);
assert.equal(historyRuleSummary(old).includes("价格过程"), false);
assert.equal(
  JSON.stringify(old),
  frozenOld,
  "opening old rules cannot enable a price process or change period",
);
assert.equal("price_path" in normalizeRuleTree(old).history_event, false);

const base = old.history_event;
assert.match(
  historyReclaimModes.find((mode) => mode.value === "close_at_or_above").label,
  /仅看今日/,
);
assert.match(
  historyReclaimModes.find((mode) => mode.value === "close_reclaim").label,
  /昨收低于、今收不低于/,
);
assert.match(
  historyRuleSummary(
    { field: "close", op: ">", right_event_field: "close" },
    (field) => (field === "close" ? "收盘价" : field),
  ),
  /观察日收盘价（日K） > 事件日收盘价/,
);
const guarded = withHistoryPricePart(base, "guard", defaultHistoryGuard());
assert.deepEqual(guarded.price_path, {
  guard: { max_drawdown_pct: 5, require_pullback: true },
});
assert.equal("price_path" in base, false);
const both = withHistoryPricePart(guarded, "reclaim", defaultHistoryReclaim());
assert.equal(both.price_path.guard, guarded.price_path.guard);
const onlyReclaim = withHistoryPricePart(both, "guard", undefined);
assert.deepEqual(onlyReclaim.price_path, {
  reclaim: { event_field: "close", mode: "close_reclaim" },
});
const disabled = withHistoryPricePart(onlyReclaim, "reclaim", undefined);
assert.equal("price_path" in disabled, false);
assert.deepEqual(disabled, base);

const todayOr = {
  any: [
    { field: "macd_cross", op: "==", value: true, period: "D" },
    { field: "kd_cross", op: "==", value: true, period: "D" },
  ],
};
const original = { ...base, then: todayOr };
const snapshot = JSON.stringify(original);
const rebound = applyHistoryPricePreset(original, "guard-rebound");
assert.equal(rebound.where, original.where);
assert.equal(rebound.window, original.window);
assert.equal(
  rebound.then.all[0],
  todayOr,
  "preserve MACD/KD OR as one atomic group",
);
assert.deepEqual(rebound.then.all[1], {
  field: "change_pct",
  op: ">=",
  value: 2,
  period: "D",
});
assert.equal(JSON.stringify(original), snapshot);
assert.deepEqual(
  applyHistoryPricePreset(rebound, "guard-rebound"),
  rebound,
  "repeated shortcut cannot stack identical daily-gain clauses",
);
const customized = {
  ...original,
  price_path: {
    guard: { max_drawdown_pct: 7.5, near_floor: { sessions: 3, band_pct: 2 } },
    reclaim: { event_field: "high", mode: "touch" },
  },
};
const customizedRebound = applyHistoryPricePreset(customized, "guard-rebound");
assert.equal(customizedRebound.price_path.guard.max_drawdown_pct, 7.5);
assert.deepEqual(
  customizedRebound.price_path.guard.near_floor,
  customized.price_path.guard.near_floor,
);
assert.deepEqual(
  customizedRebound.price_path.reclaim,
  customized.price_path.reclaim,
);
const reclaim = applyHistoryPricePreset(customized, "reclaim");
assert.equal(reclaim.then, original.then);
assert.equal(reclaim.where, original.where);
assert.equal(reclaim.price_path.reclaim.event_field, "high");
assert.equal(reclaim.price_path.reclaim.mode, "close_reclaim");
assert.equal(reclaim.price_path.guard, customized.price_path.guard);

assert.equal(validateHistoryPricePath(undefined), null);
for (const drawdown of [0, 5, 5.5, 99.999])
  for (const require_pullback of [undefined, false, true]) {
    const guard = {
      max_drawdown_pct: drawdown,
      ...(require_pullback === undefined ? {} : { require_pullback }),
    };
    assert.equal(validateHistoryPricePath({ guard }), null);
    for (const sessions of [1, 5, 750])
      for (const band_pct of [0, 1, 2.25, 100])
        assert.equal(
          validateHistoryPricePath({
            guard: { ...guard, near_floor: { sessions, band_pct } },
          }),
          null,
        );
  }
for (const mode of historyReclaimModes)
  for (const event_field of ["close", "high", "low"]) {
    const event = {
      ...base,
      price_path: { reclaim: { event_field, mode: mode.value } },
    };
    assert.equal(validateVisualRule({ history_event: event }), null);
    assert.deepEqual(normalizeRuleTree({ history_event: event }), {
      history_event: event,
    });
    assert.match(historyPricePathSummary(event.price_path), /价格过程（日K）/);
  }
for (const invalid of [
  null,
  [],
  {},
  { extra: {} },
  { guard: undefined },
  { reclaim: undefined },
  { guard: { max_drawdown_pct: "" } },
  { guard: { max_drawdown_pct: "5" } },
  { guard: { max_drawdown_pct: true } },
  { guard: { max_drawdown_pct: -1 } },
  { guard: { max_drawdown_pct: 100 } },
  { guard: { max_drawdown_pct: Infinity } },
  { guard: { max_drawdown_pct: 5, require_pullback: "true" } },
  ...[0, 751, 1.5, "", "1"].map((sessions) => ({
    guard: { max_drawdown_pct: 5, near_floor: { sessions, band_pct: 1 } },
  })),
  ...[-1, 101, "", NaN].map((band_pct) => ({
    guard: { max_drawdown_pct: 5, near_floor: { sessions: 1, band_pct } },
  })),
  { reclaim: { mode: "unsupported", event_field: "close" } },
  { reclaim: { mode: "touch", event_field: "open" } },
  { reclaim: { mode: "touch", event_field: "close", extra: true } },
])
  assert.equal(
    typeof validateHistoryPricePath(invalid),
    "string",
    `reject invalid path ${JSON.stringify(invalid)}`,
  );

const summary = historyRuleSummary({
  history_event: {
    ...base,
    price_path: {
      guard: {
        max_drawdown_pct: 5,
        require_pullback: true,
        near_floor: { sessions: 1, band_pct: 1 },
      },
      reclaim: { event_field: "close", mode: "first_close_reclaim" },
    },
  },
});
assert.match(summary, /事件收盘×\(1−5%\)/);
assert.match(summary, /昨日曾收盘落在保护价/);
assert.match(summary, /首次重新收复/);
assert.match(summary, /同时满足今日条件/);
assert.match(
  historyCheckText({
    window: { from: 1, to: 30 },
    result: null,
    price_path_checks: { result: null },
  }),
  /价格过程待核验/,
);
assert.match(
  historyCheckText({
    window: { from: 1, to: 30 },
    result: false,
    price_path_checks: { result: false },
  }),
  /价格过程未满足/,
);
assert.match(
  historyCheckText({
    window: { from: 1, to: 30 },
    selected_event: { date: "2026-10-12" },
    result: false,
    checks: [{ result: true }],
    price_path_checks: { result: false },
  }),
  /整体条件未满足；价格过程未满足/,
);
assert.match(
  historyCheckText({
    window: { from: 1, to: 30 },
    selected_event: { date: "2026-10-12" },
    result: false,
    checks: [{ result: false }],
    price_path_checks: { result: true },
  }),
  /整体条件未满足；价格过程满足/,
);
assert.equal(JSON.stringify(old), frozenOld);
console.log(
  "history price path: optional roundtrip, guarded defaults, atomic OR shortcuts, daily gain, modes, editable validation and summaries passed",
);
