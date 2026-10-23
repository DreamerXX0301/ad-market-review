/** Exercise the actual shared condition text without mounting React or writing data. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
const moduleUrl = (source) =>
  "data:text/javascript;base64," +
  Buffer.from(compile(source)).toString("base64");
const visibilityUrl = moduleUrl(
  await fs.readFile(
    new URL("../../lib/indicatorVisibility.ts", import.meta.url),
    "utf8",
  ),
);
const formatUrl = moduleUrl(
  (
    await fs.readFile(new URL("../../lib/format.ts", import.meta.url), "utf8")
  ).replace('"./indicatorVisibility"', JSON.stringify(visibilityUrl)),
);
const evidenceUrl = moduleUrl(
  await fs.readFile(
    new URL("../strategies/conditionEvidence.ts", import.meta.url),
    "utf8",
  ),
);
const historicalUrl = moduleUrl(
  await fs.readFile(
    new URL("../strategies/historicalRules.ts", import.meta.url),
    "utf8",
  ),
);
const source = await fs.readFile(
  new URL("../strategies/Conditions.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Conditions.tsx",
  source,
  ts.ScriptTarget.ES2022,
  true,
  ts.ScriptKind.TSX,
);
const names = new Set([
  "ruleValue",
  "fieldLabel",
  "entryHighEvidenceText",
  "entryLowEvidenceText",
  "summarizeChecks",
]);
const functions = ast.statements.filter(
  (node) =>
    (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) ||
    (ts.isVariableStatement(node) &&
      node.declarationList.declarations.some(
        (declaration) => declaration.name.getText(ast) === "ruleOperator",
      )),
);
const helpers = await import(
  moduleUrl(
    `import {num,textValue} from ${JSON.stringify(formatUrl)}; import {yearlyLimitCountText} from ${JSON.stringify(evidenceUrl)}; import {historyCheckText,historyRuleSummary,conditionReasonText,HIDDEN_CONDITION_TEXT,isHiddenConditionField,isHiddenConditionRule} from ${JSON.stringify(historicalUrl)};\n` +
      functions.map((node) => node.getText(ast)).join("\n"),
  )
);
assert.equal(helpers.ruleOperator("new_high"), "回看");
assert.equal(helpers.fieldLabel("kdj_j", "KDJ J"), "已隐藏的指标");
for (const check of [
  { field: "kdj_j", op: ">", value: 54.78, actual: 60.25 },
  {
    field: "kdj_k",
    op: ">",
    right_field: "kdj_j",
    actual: 60.25,
    right_actual: 54.78,
  },
]) {
  const snapshot = JSON.stringify(check);
  assert.equal(
    helpers.summarizeChecks(check),
    "已隐藏的指标条件（保留原配置）",
  );
  assert.equal(JSON.stringify(check), snapshot);
}
assert.match(
  helpers.summarizeChecks({
    type: "history_event",
    result: true,
    window: { from: 5, to: 5 },
    selected_event: { date: "2026-10-12", offset: 5 },
    checks: [
      {
        field: "close",
        op: ">",
        right_event_field: "high",
        actual: 11,
        right_actual: 10,
        result: true,
      },
    ],
  }),
  /2026-10-12 · 5个交易日前；当前条件满足/,
);
assert.match(
  helpers.summarizeChecks({
    field: "close",
    op: ">",
    right_event_field: "high",
    actual: 11,
    right_actual: 10,
    event_date: "2026-10-12",
    result: true,
  }),
  /观察日收盘价（日K） 11.00 > 事件日最高价 10.00/,
);
assert.equal(helpers.ruleValue("close_breakout_high", 60), "60个交易日");
assert.equal(
  helpers.summarizeChecks({
    field: "close_breakout_high",
    op: "new_high",
    value: 60,
    actual: true,
    result: true,
    lookback_sessions: 60,
    covered_sessions: 60,
  }),
  "收盘价突破前60个交易日最高价（是；已覆盖60/60日）",
);
assert.equal(
  helpers.summarizeChecks({
    field: "close_breakout_high",
    op: "new_high",
    value: 10,
    actual: false,
    result: false,
  }),
  "收盘价突破前10个交易日最高价（否）",
);
const unknown = helpers.summarizeChecks({
  field: "close_breakout_high",
  op: "new_high",
  value: 60,
  actual: null,
  result: null,
  covered_sessions: 59,
  reason: "窗口报价缺失",
});
assert.match(unknown, /待核验；已覆盖59\/60日；窗口报价缺失/);
assert.equal(unknown.includes("new_high"), false);
assert.match(
  helpers.entryHighEvidenceText({
    field: "close_above_entry_high",
    actual: true,
    evidence: {
      entry_high: { state: "ready", date: "2026-10-19", raw_high: 12.35 },
    },
  }),
  /实际是；事件日 2026-10-19 最高价 12.35/,
);
assert.match(
  helpers.entryHighEvidenceText({
    field: "close_above_entry_high",
    actual: null,
    evidence: { entry_high: { state: "unknown", raw_high: 0 } },
  }),
  /实际待核验；事件日最高价待核验/,
);
assert.equal(
  helpers.entryHighEvidenceText({ field: "close", actual: 3 }),
  null,
);
console.log(
  "strategy condition display: parameterized daily windows, Boolean result evidence, unknown coverage and frozen entry-high proof passed",
);
