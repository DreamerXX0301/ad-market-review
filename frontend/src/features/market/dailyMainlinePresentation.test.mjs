import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./dailyMainlinePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  buildDailyMainlineGroups,
  emptyMainlineAttribution,
  editableAttribution,
  mainlineWritePayload,
  mainlineEvidenceUrl,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const date = "2026-01-05";
const record = (id, state, name = null, extra = {}) => ({
  date,
  instrument_id: id,
  revision: 1,
  display_state: state,
  display_mainline: name,
  attributions: [],
  secondary_mainlines: [],
  ...extra,
});
const stock = (id) => ({
  instrument_id: id,
  name: "测试证券",
  topics: [{ name: "人数最多概念" }],
  reason: "测试关键词+公司名",
});
const rows = [
  stock("600004.SH"),
  stock("600001.SH"),
  stock("000001.SZ"),
  stock("600002.SH"),
  stock("600003.SH"),
  stock("600001.SH"),
];
const records = [
  record("600001.SH", "attributed", "有独立证据的主线", {
    secondary_mainlines: ["次级主线"],
  }),
  record("000001.SZ", "primary_pending", null, {
    secondary_mainlines: ["甲", "乙"],
  }),
  record("600002.SH", "conflict"),
  record("600003.SH", "unattributed", null, { action: "revoke" }),
  record("600004.SH", "attributed", "另一日期不可回填", { date: "2026-01-06" }),
  record("600005.SH", "attributed", "池外已存记录"),
  record("600001.SH", "attributed", "已被修订的旧主线", { revision: 0 }),
];
const before = structuredClone({ rows, records });
const plan = buildDailyMainlineGroups(date, rows, records);
assert.equal(
  plan.total,
  5,
  "count unique confirmed-pool identities, not stored claims",
);
assert.equal(plan.attributed, 1);
assert.deepEqual(
  plan.groups.map((g) => g.name),
  ["有独立证据的主线", "主组待定", "来源冲突", "未归因"],
);
assert.equal(
  plan.groups.flatMap((g) => g.items).length,
  5,
  "one stock has one display position even with secondary affiliations",
);
assert.equal(plan.groups.find((g) => g.name === "未归因").items.length, 2);
assert.equal(plan.outside.length, 1);
assert.equal(plan.outside[0].instrument_id, "600005.SH");
assert.deepEqual(
  { rows, records },
  before,
  "presentation cannot rewrite source identities, concepts or attribution records",
);
const noRecords = buildDailyMainlineGroups(date, rows, []);
assert.deepEqual(
  noRecords.groups.map((g) => g.name),
  ["未归因"],
  "never infer a daily mainline from company name, keywords or concept size",
);
const evidence = {
  ...emptyMainlineAttribution(),
  mainline_name: "测试主线",
  assertion_id: "server-owned",
  first_known_at_ms: 123,
  source_kind: "fake-official",
  recorded_at_ms: 1,
};
const editable = editableAttribution(evidence);
assert.equal(editable.first_known_at_ms, undefined);
assert.equal(editable.assertion_id, undefined);
assert.equal(editable.source_kind, undefined);
const payload = mainlineWritePayload(
  date,
  "600001.SH",
  7,
  "replace",
  "更正理由",
  [evidence],
);
assert.equal(payload.expected_revision, 7);
assert.deepEqual(payload.attributions, [editable]);
assert.deepEqual(
  mainlineWritePayload(date, "600001.SH", 7, "revoke", "撤销理由", [evidence])
    .attributions,
  [],
);
assert.equal(
  mainlineEvidenceUrl("https://example.com/evidence#row-1"),
  "https://example.com/evidence#row-1",
);
for (const reference of [
  "javascript:alert(1)",
  "data:text/html,x",
  "file:///private/test",
  "fixture:image#row",
  "/local/evidence.png",
  "not-a-url",
])
  assert.equal(
    mainlineEvidenceUrl(reference),
    null,
    "non-web evidence is text only and never loaded automatically",
  );
console.log(
  "daily mainline grouping, evidence links and write boundary passed",
);
