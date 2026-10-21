import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(new URL("./conditionPeriod.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  conditionPeriodView,
  withConditionPeriod,
  conditionPeriodSummary,
  metricCycleLabel,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const old = { field: "macd_cross", op: "==", value: true };
assert.equal(conditionPeriodView(old, { type: "boolean" }, "M").effective, "M");
assert.deepEqual(
  old,
  { field: "macd_cross", op: "==", value: true },
  "opening a legacy leaf does not add period",
);
assert.deepEqual(withConditionPeriod(old, "W"), { ...old, period: "W" });
assert.deepEqual(withConditionPeriod({ ...old, period: "M" }, "inherit"), old);
assert.equal(
  conditionPeriodView({ ...old, period: "W" }, {}, "D").effective,
  "W",
);
assert.equal(
  conditionPeriodView({ field: "limit_up" }, {}, "M").effective,
  "D",
);
assert.equal(
  conditionPeriodView({ field: "close_breakout_high" }, {}, "W").effective,
  "D",
);
assert.equal(
  conditionPeriodView({ field: "close", right_event_field: "high" }, {}, "M")
    .effective,
  "D",
);
assert.equal(
  conditionPeriodView(old, {}, "W", true).effective,
  "D",
  "historical where remains daily",
);
assert.equal(conditionPeriodView({ field: "is_st" }, {}, "M").identity, true);
assert.equal(conditionPeriodSummary(old), "");
assert.match(conditionPeriodSummary({ ...old, period: "M" }), /完整月K/);
console.log("condition period: passed");
assert.equal(
  metricCycleLabel("volume_ratio", "较昨日成交量倍数", "M"),
  "较前一完整月成交量倍数",
);
assert.equal(
  metricCycleLabel("volume_ratio", "较昨日成交量倍数", "W"),
  "较前一完整周成交量倍数",
);
assert.equal(
  metricCycleLabel("cross_above_ma30", "今日上穿MA30", "M"),
  "当月收盘上穿 MA30",
);
assert.equal(
  metricCycleLabel("cross_above_ma5", "今日上穿MA5", "W"),
  "当周收盘上穿 MA5",
);
assert.equal(
  metricCycleLabel("cross_above_ma5", "上穿MA5", "D"),
  "今日收盘上穿 MA5",
);
assert.equal(
  metricCycleLabel("limit_up", "今日是否涨停", "M"),
  "今日是否涨停",
  "daily-only predicate labels stay daily",
);
