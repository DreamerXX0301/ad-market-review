import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./sectorPresentation.ts", import.meta.url),
  "utf8",
);
const compile = (source) => ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const moduleUrl = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
let compiled = compile(source);
for (const dependency of [
  "../../lib/indicatorVisibility",
  "../strategies/historicalRules",
]) {
  const code = await fs.readFile(
    new URL(dependency + ".ts", import.meta.url),
    "utf8",
  );
  compiled = compiled.replace(
    JSON.stringify(dependency),
    JSON.stringify(moduleUrl(compile(code))),
  );
}
const {
  sectorDefaultLeaf,
  sectorStrategyBody,
  sectorLeafEditorKind,
  validateSectorRule,
  sectorRuleText,
  sectorPolicyTree,
  sectorPolicyFromTrees,
  memberQuoteDate,
  sectorMemberNavigationRows,
  sectorMemberBoard,
  sectorMembersEndpoint,
  sectorCheckLeaves,
  sectorSelectableFields,
} = await import(moduleUrl(compiled));
const zoneRule = {
  field: "horizontal_support_close_near",
  op: "matches",
  value: 0.5,
  period: "W",
};
const zoneFields = [{
  field: zoneRule.field,
  type: "number",
  operators: ["matches"],
  constant_only: true,
  selection_only: true,
  min: 0,
  max: 10,
}];
assert.equal(validateSectorRule(zoneRule, zoneFields), null);
assert.match(validateSectorRule({ ...zoneRule, value: 20 }, zoneFields), /超出允许范围/);
assert.equal(
  sectorRuleText(zoneRule, zoneFields),
  "完整周K · 收盘靠近水平支撑区 · 容差 0.5%",
);
const fields = [
  {
    field: "close",
    label: "收盘点位",
    type: "number",
    operators: [">", ">="],
    periods: ["D", "W", "M"],
  },
  {
    field: "ma60",
    label: "MA60",
    type: "number",
    operators: [">", ">="],
    periods: ["D", "W", "M"],
  },
  {
    field: "close_breakout_high",
    label: "N日新高",
    type: "number",
    operators: ["new_high"],
    periods: ["D"],
    fixed_period: "D",
    parameter_kind: "lookback_sessions",
    constant_only: true,
    integer: true,
    min: 1,
    max: 750,
    default: 60,
  },
  {
    field: "intraday_high_change_pct",
    label: "观察日最高涨幅",
    type: "number",
    operators: [">="],
    fixed_period: "D",
    periods: ["D"],
  },
  {
    field: "macd_cross",
    label: "MACD金叉",
    type: "boolean",
    operators: ["=="],
    periods: ["D", "W", "M"],
  },
  {
    field: "kd_cross",
    label: "KDJ金叉",
    type: "boolean",
    operators: ["=="],
    periods: ["D", "W", "M"],
  },
  {
    field: "close_above_entry_high",
    label: "高于入池日最高点位",
    type: "boolean",
    operators: ["=="],
    fixed_period: "D",
    periods: ["D"],
    tracking_only: true,
    alerts_only: true,
  },
];
const rule = {
  all: [
    { field: "close_breakout_high", op: "new_high", value: 60, period: "D" },
    { field: "macd_cross", op: "==", value: true, period: "W" },
    { field: "kd_cross", op: "==", value: true, period: "M" },
  ],
};
assert.equal(validateSectorRule(rule, fields, "M"), null);
assert.equal(sectorDefaultLeaf(fields, "M").period, "D");
assert.equal(
  sectorDefaultLeaf([fields[0]], "M").period,
  undefined,
  "new ordinary leaves inherit strategy cycle",
);
assert.match(sectorRuleText(rule, fields, "D"), /完整月K|月K/);
assert.match(sectorRuleText(rule, fields, "D"), /周K/);
assert.match(
  validateSectorRule({ field: "is_st", op: "==", value: false }, fields),
  /不支持/,
);
assert.match(validateSectorRule({ history_event: {} }, fields), /只支持/);
assert.match(
  validateSectorRule({ ...rule.all[0], period: "W" }, fields),
  /周期/,
);
assert.match(
  validateSectorRule({ ...rule.all[0], value: 751 }, fields),
  /范围/,
);
assert.match(
  validateSectorRule({ ...rule.all[0], value: 60.5 }, fields),
  /整数/,
);
assert.match(
  validateSectorRule({ ...rule.all[1], value: 1 }, fields),
  /是或否/,
);
assert.match(
  validateSectorRule({ field: "close", op: ">", value: null }, fields),
  /有效数值/,
);
assert.equal(
  validateSectorRule(
    { field: "close", op: ">", right_field: "ma60", period: "M" },
    fields,
  ),
  null,
);
assert.match(
  validateSectorRule(
    { field: "close", op: ">", right_field: "ma60", value: 0 },
    fields,
  ),
  /同周期/,
);
assert.equal(sectorLeafEditorKind(fields[2]), "lookback");
assert.equal(
  sectorLeafEditorKind(fields[3]),
  "number",
  "fixed daily percentage is not an N-day window",
);
assert.equal(
  sectorLeafEditorKind(fields[6]),
  "boolean",
  "daily entry anchor keeps boolean editor",
);
const nested = {
  any: [rule, { field: "close", op: ">=", value: 0, period: "M" }],
};
const policy = sectorPolicyFromTrees(
  sectorPolicyTree([nested], "ALL"),
  sectorPolicyTree([], "ANY"),
);
assert.equal(policy.alert_logic, "ALL");
assert.equal(policy.exit_logic, "ANY");
assert.deepEqual(policy.alerts, [nested]);
assert.deepEqual(policy.exits, []);
assert.equal(
  memberQuoteDate({
    quote_as_of: "2026-10-20",
    membership_as_of: "2026-10-21",
  }),
  "2026-10-20",
);
assert.equal(
  memberQuoteDate({ membership_as_of: "2026-10-21" }),
  null,
  "membership date cannot substitute quote date",
);
assert.equal(memberQuoteDate({ quote_as_of: 0 }), null);
assert.deepEqual(
  sectorMemberNavigationRows([
    {
      instrument_id: "600000.SH",
      name: "测试",
      code: "600000",
      eventBaseline: { rawClose: 9000 },
      stock_entry_baseline: null,
    },
  ]),
  [{ instrument_id: "600000.SH", name: "测试", code: "600000" }],
  "strip all sector baseline context",
);
assert.deepEqual(
  sectorCheckLeaves({
    checks: [
      { field: "close", actual: 0, result: false },
      { checks: [{ field: "macd_cross", actual: null, result: null }] },
    ],
  }).map((item) => item.actual),
  [0, null],
);
console.log("sector presentation: passed");
assert.equal(
  sectorMembersEndpoint({
    topicCode: "TI885333",
    asOf: "2026-10-20",
    scope: "latest",
  }),
  "/sectors/TI885333/members?membership_scope=latest&as_of=2026-10-20",
  "standalone search members do not require or create a tracking round",
);
assert.equal(
  sectorMembersEndpoint({
    roundId: "round-1",
    asOf: "2026-10-19",
    scope: "historical",
  }),
  "/sector-tracking-rounds/round-1/members?membership_scope=historical&as_of=2026-10-19",
);
assert.equal(
  sectorMembersEndpoint({ topicCode: "TI885333", asOf: "", scope: "latest" }),
  null,
);
assert.equal(
  sectorMembersEndpoint({ asOf: "2026-10-20", scope: "latest" }),
  null,
);
assert.equal(
  sectorMemberBoard({ board: "star", board_label: "科创板" }).board,
  "star",
);
assert.equal(sectorMemberBoard({ board_name: "创业板" }).board, "chinext");
assert.equal(
  sectorMemberBoard({ board: "unknown", board_name: "主板" }).board,
  "unknown",
  "explicit unknown cannot become a known board",
);
assert.equal(sectorMemberBoard({}).board, "unknown");

const createBody = sectorStrategyBody({
  name: " 三周期板块 ",
  period: "M",
  sectorKind: "all",
  definition: rule,
  duration: 30,
});
assert.equal(createBody.name, "三周期板块");
assert.equal(createBody.policy.alert_logic, "ANY");
assert.equal(createBody.policy.exit_logic, "ANY");
assert.equal(createBody.policy.duration_sessions, 30);
assert.deepEqual(createBody.definition, rule);
assert.equal(createBody.period, "M");
const savedPolicy = {
  alerts: [nested],
  exits: [],
  alert_logic: "ALL",
  exit_logic: "ANY",
  duration_sessions: 60,
  allow_entry_day_alerts: true,
};
const editBody = sectorStrategyBody({
  name: "保留默认",
  period: "W",
  sectorKind: "concept",
  definition: rule,
  duration: 750,
  existingPolicy: savedPolicy,
});
assert.deepEqual(editBody.policy.alerts, [nested]);
assert.equal(editBody.policy.alert_logic, "ALL");
assert.equal(
  savedPolicy.duration_sessions,
  60,
  "editing does not mutate the saved policy",
);

const hiddenFields = [
  { field: "kdj_j", label: "KDJ J", type: "number", operators: [">="] },
  fields[0],
];
assert.deepEqual(sectorSelectableFields(hiddenFields), [fields[0]]);
assert.equal(sectorDefaultLeaf(hiddenFields).field, "close");
assert.deepEqual(sectorDefaultLeaf([hiddenFields[0]]), {});
const hiddenRule = { field: "kdj_j", op: ">=", value: 93.2 };
assert.equal(
  sectorRuleText(hiddenRule, hiddenFields),
  "已隐藏的指标条件（保留原配置）",
);
assert.equal(
  sectorRuleText(
    { field: "close", op: ">", right_field: "kdj_j" },
    hiddenFields,
  ),
  "已隐藏的指标条件（保留原配置）",
);
const visibleChecks = sectorCheckLeaves({
  checks: [
    { ...hiddenRule, actual: 94, result: true },
    { field: "kdj_k", actual: 0, result: false },
    { field: "kdj_d", actual: null, result: null },
  ],
});
assert.deepEqual(visibleChecks[0], {
  hidden: true,
  description: "已隐藏的指标条件（保留原配置）",
});
assert.equal(visibleChecks[1].actual, 0);
assert.equal(visibleChecks[2].actual, null);
assert.equal(hiddenRule.value, 93.2, "the saved rule is preserved");
