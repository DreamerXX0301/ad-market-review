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
  historyFieldsFallback,
  isHiddenConditionField,
  isHiddenConditionRule,
  hasHiddenConditionRule,
  HIDDEN_CONDITION_TEXT,
  conditionReasonText,
  historyWindowMode,
  historyWindowForMode,
  historyMatchOptions,
  historyMatchDescription,
  historyEventPreset,
  historyPresetWhere,
  appendTodayCondition,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const first = newHistoryRule(),
  second = newHistoryRule();
first.history_event.window.from = 8;
assert.equal(
  second.history_event.window.from,
  1,
  "each event keeps its own binding",
);
assert.equal(validateVisualRule(second), null);
assert.equal(second.history_event.match, "latest", "new ordinary rules keep the established default");
assert.deepEqual(historyMatchOptions.map(item => item.value), ["latest", "earliest", "any"]);
assert.match(historyMatchDescription("any"), /从近到远/);
assert.match(historyMatchDescription("any"), /全部条件使用同一事件/);
assert.match(historyMatchDescription("any"), /仍有事件无法判断整体是否满足/);
assert.match(historyMatchDescription("latest"), /整体不满足时不会改选/);
assert.match(historyMatchDescription("earliest"), /更早日期待核验/);
assert.equal(second.history_event.where.all[0].period, "D");
assert.equal(second.history_event.then.all[0].period, "D");
assert.equal(second.history_event.window.to, 30);
assert.equal(second.history_event.then.all[0].field, "change_pct");
assert.equal(historyWindowMode(second.history_event.window), "recent");
assert.equal(historyWindowMode({ from: 5, to: 20 }), "range");
assert.equal(historyWindowMode({ from: 30, to: 30 }), "exact");
assert.deepEqual(historyWindowForMode({ from: 5, to: 20 }, "recent"), {
  from: 1,
  to: 20,
});
assert.deepEqual(historyWindowForMode({ from: 1, to: 30 }, "exact"), {
  from: 30,
  to: 30,
});
assert.deepEqual(historyWindowForMode({ from: 5, to: 20 }, "range"), {
  from: 5,
  to: 20,
});
assert.equal(historyEventPreset(historyPresetWhere("volume")), "volume");
assert.equal(historyEventPreset(historyPresetWhere("limit")), "limit");
assert.equal(historyFieldsFallback.has("limit_up"), true);
assert.equal(historyFieldsFallback.has("prev_limit_up"), false);
assert.equal(
  validateVisualRule({
    history_event: {
      ...second.history_event,
      window: { from: 5, to: 20 },
      where: historyPresetWhere("limit"),
    },
  }),
  null,
);
const originalToday = {
  any: [
    { field: "macd_cross", op: "==", value: true, period: "D" },
    { field: "kd_cross", op: "==", value: true, period: "D" },
  ],
};
const appended = appendTodayCondition(originalToday, {
  field: "change_pct",
  op: ">",
  value: 3,
});
assert.deepEqual(
  appended.all[0],
  originalToday,
  "adding an AND clause preserves the existing OR group",
);
assert.equal(appended.all[1].period, "D");
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
const anyEvent = structuredClone(second);
anyEvent.history_event.match = "any";
const anyBefore = JSON.stringify(anyEvent);
assert.equal(validateVisualRule(anyEvent), null);
assert.deepEqual(normalizeRuleTree(anyEvent), anyEvent);
assert.match(historyRuleSummary(anyEvent), /范围内任意一次满足/);
assert.match(historyRuleSummary(anyEvent), /绑定从近到远找到的第一个已确认整体满足事件/);
assert.match(historyRuleSummary(anyEvent), /全部条件使用同一事件/);
assert.equal(JSON.stringify(anyEvent), anyBefore, "presentation and validation never rewrite a saved match");
assert.match(validateVisualRule({ history_event: { ...anyEvent.history_event, match: "all" } }), /匹配方式/);
assert.match(historyCheckText({ ...anyEvent, window: { from: 1, to: 30 }, result: true, selected_event: { date: "2026-10-12", offset: 6 } }), /2026-10-12 · 6个交易日前 · 最近一次已确认整体满足；整体条件满足/);
for (const result of [false, null]) {
  const summary = historyCheckText({ ...anyEvent, window: { from: 1, to: 30 }, result, selected_event: { date: "2026-10-12", offset: 6 } });
  assert.match(summary, /范围内任意一次/);
  assert.doesNotMatch(summary, /2026-10-12|最近一次已确认整体满足/, "unconfirmed any searches have no selected-event label");
}
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
assert.equal(normalized.all[0].history_event.then.all[0].field, "change_pct");
assert.equal(normalized.all[1].any[1].field, "macd_cross");
assert.equal(validateVisualRule(normalized), null);
assert.match(
  historyRuleSummary(
    oldEvent,
    (field) => ({ close: "收盘价", high: "最高价" })[field] || field,
  ),
  /收盘价 > ma30/,
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
wrong.history_event.then.all[0] = {
  field: "high",
  op: ">",
  right_event_field: "close",
};
assert.match(validateVisualRule(wrong), /观察日收盘价/);
assert.match(
  validateVisualRule({ field: "close", op: ">", right_event_field: "high" }),
  /只能用于/,
);
const nested = newHistoryRule();
nested.history_event.where = { all: [newHistoryRule()] };
assert.match(validateVisualRule(nested), /不能再次嵌套/);
const ambiguous = newHistoryRule();
ambiguous.history_event.then.all[0].right_event_field = "close";
ambiguous.history_event.then.all[0].field = "close";
assert.match(validateVisualRule(ambiguous), /只能选择一种/);
const empty = newHistoryRule();
empty.history_event.where = { all: [] };
assert.match(validateVisualRule(empty), /至少一项/);
assert.equal(isHiddenConditionField("kdj_j"), true);
assert.equal(isHiddenConditionField("kdj_k"), false);
assert.equal(isHiddenConditionField("kdj_d"), false);
assert.equal(historyFieldsFallback.has("kdj_j"), false);
for (const period of ["D", "W", "M"]) {
  for (const rule of [
    { field: "kdj_j", op: ">", value: 54.78, period },
    { field: "kdj_k", op: ">", right_field: "kdj_j", period },
  ]) {
    const snapshot = JSON.stringify(rule);
    assert.equal(isHiddenConditionRule(rule), true);
    assert.equal(historyRuleSummary(rule), HIDDEN_CONDITION_TEXT);
    const event = newHistoryRule();
    event.history_event.where = { all: [rule] };
    const tree = { any: [event, current] };
    assert.equal(hasHiddenConditionRule(tree), true);
    assert.equal(historyRuleSummary(tree).includes("kdj_j"), false);
    assert.equal(historyRuleSummary(tree).includes("54.78"), false);
    assert.equal(
      JSON.stringify(rule),
      snapshot,
      "presentation does not mutate saved execution",
    );
    assert.equal(
      validateVisualRule(rule),
      null,
      "existing rules remain valid and retain semantics",
    );
    assert.deepEqual(normalizeRuleTree(rule), rule);
  }
}
assert.equal(hasHiddenConditionRule({ all: [current] }), false);
for (const reason of [
  "kdj_j 预热不足",
  "KDJ J 54.78",
  "J值 54.78",
  "J线待核验",
])
  assert.equal(conditionReasonText(reason), "部分指标条件已隐藏");
assert.equal(conditionReasonText("周线预热不足"), "周线预热不足");
assert.doesNotMatch(
  historyCheckText({
    result: null,
    window: { from: 1, to: 30 },
    reason: "kdj_j 54.78 待核验",
  }),
  /kdj_j|54\.78/,
);
console.log(
  "historical rules: isolated bindings, nested boolean roundtrip, dated summaries and invalid windows/RHS preserved safely",
);
