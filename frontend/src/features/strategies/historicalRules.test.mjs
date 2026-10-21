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
const {
  newHistoryRule,
  normalizeRuleTree,
  historyRuleSummary,
  historyCheckText,
  historyWindowText,
  ruleCount,
  validateVisualRule,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const first = newHistoryRule(),
  second = newHistoryRule();
first.history_event.window.from = 8;
assert.equal(
  second.history_event.window.from,
  30,
  "each event keeps its own binding",
);
assert.equal(validateVisualRule(second), null);
assert.equal(second.history_event.where.all[0].period, "D");
assert.equal(second.history_event.then.all[0].period, "D");
const oldEvent = {
  history_event: {
    window: { from: 30, to: 30 },
    match: "latest",
    where: { all: [{ field: "volume_ratio", op: ">=", value: 3 }] },
    then: { all: [{ field: "close", op: ">", right_field: "ma30" }] },
  },
};
assert.deepEqual(
  normalizeRuleTree(oldEvent),
  oldEvent,
  "legacy events retain absent scopes and prior defaults",
);
assert.equal(historyWindowText({ from: 5, to: 5 }), "恰好5个交易日前");
assert.equal(historyWindowText({ from: 1, to: 30 }), "距今1—30个交易日");
const current = { field: "change_pct", op: ">=", value: 3 };
const normalized = normalizeRuleTree({
  logic: "AND",
  conditions: [
    second,
    {
      logic: "OR",
      conditions: [current, { field: "macd_cross", op: "==", value: true }],
    },
  ],
});
assert.equal(ruleCount(normalized), 3);
assert.equal(
  normalized.all[0].history_event.then.all[0].right_event_field,
  "close",
);
assert.equal(normalized.all[1].any[1].field, "macd_cross");
assert.equal(validateVisualRule(normalized), null);
assert.match(
  historyRuleSummary(
    normalized,
    (field) => ({ close: "收盘价", high: "最高价" })[field] || field,
  ),
  /观察日收盘价（日K） > 事件日收盘价/,
);
assert.match(historyRuleSummary(normalized), / 或 /);
assert.match(
  historyCheckText({
    type: "history_event",
    window: { from: 1, to: 30 },
    selected_event: { date: "2026-10-12", offset: 6 },
    result: true,
  }),
  /2026-10-12 · 6个交易日前；当前条件满足/,
);
assert.match(
  historyCheckText({
    type: "history_event",
    window: { from: 1, to: 30 },
    selected_event: null,
    result: null,
    reason: "更近事件未知",
  }),
  /待核验；更近事件未知/,
);
for (const window of [
  { from: 0, to: 30 },
  { from: 31, to: 30 },
  { from: 1, to: 751 },
  { from: 1.5, to: 2 },
  { from: "5", to: 5 },
]) {
  const event = newHistoryRule();
  event.history_event.window = window;
  assert.match(validateVisualRule(event), /历史范围/);
}
const wrong = newHistoryRule();
wrong.history_event.then.all[0].field = "high";
assert.match(validateVisualRule(wrong), /观察日收盘价/);
assert.match(
  validateVisualRule({ field: "close", op: ">", right_event_field: "high" }),
  /只能用于/,
);
const nested = newHistoryRule();
nested.history_event.where = { all: [newHistoryRule()] };
assert.match(validateVisualRule(nested), /不能再次嵌套/);
const ambiguous = newHistoryRule();
ambiguous.history_event.then.all[0].value = 1;
assert.match(validateVisualRule(ambiguous), /只能选择一种/);
const empty = newHistoryRule();
empty.history_event.where = { all: [] };
assert.match(validateVisualRule(empty), /至少一项/);
console.log(
  "historical rules: isolated bindings, nested boolean roundtrip, dated summaries and invalid windows/RHS preserved safely",
);
