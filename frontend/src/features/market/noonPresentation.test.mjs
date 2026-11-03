import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./noonPresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const {
  noonIndexAmountDelta, noonNumber, noonRows, noonLines, noonPayload, noonCaptureState,
  noonShouldPoll, noonCalendarDates, noonClock, noonSortedRows, noonPreviousText, noonChartDate, noonSourceCaption, noonCanResume, noonPreviousFacts, noonInitialDate, noonSectorSelection,
} = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const day = "2026-10-28";
assert.equal(noonInitialDate("#noon?date=2026-10-28"), day);
assert.equal(noonInitialDate("#noon?date=2024-02-29"), "2024-02-29");
assert.equal(noonInitialDate("#noon?date=2026-02-30"), "");
assert.equal(noonInitialDate("#noon?date=2026-2-3"), "");
assert.equal(noonInitialDate("#market?date=2026-10-28"), "");
assert.equal(noonInitialDate("#noon"), "");
assert.equal(noonInitialDate("#noon?date=invalid"), "");
const view = {
  date: day, today: day, state: "not_collected", locked_for_day: false,
  can_capture: true, capture_window: { allowed: true },
};
assert.equal(noonNumber(null), null);
assert.equal(noonNumber("0"), null);
assert.equal(noonNumber(NaN), null);
assert.equal(noonNumber(0), 0);
const comparison = { state: "snapshot_comparison", current_amount: 85_510_000_000,
  previous_amount: 100_000_000_000, ratio: 0.86 };
assert.equal(noonIndexAmountDelta(comparison), -14_490_000_000);
assert.equal(noonIndexAmountDelta({ ...comparison, state: "matched_period", current_amount: 101_000_000_000 }), 1_000_000_000);
assert.equal(noonIndexAmountDelta({ ...comparison, current_amount: 0 }), -100_000_000_000);
assert.equal(noonIndexAmountDelta({ ...comparison, current_amount: comparison.previous_amount }), 0);
assert.equal(noonIndexAmountDelta({ ...comparison, state: "unavailable" }), null);
assert.equal(noonIndexAmountDelta({ ...comparison, previous_amount: 0 }), null);
assert.equal(noonIndexAmountDelta({ ...comparison, current_amount: null }), null);
assert.deepEqual(noonRows({ items: [null, "bad", { count: 0 }] }), [{ count: 0 }]);
assert.deepEqual(noonLines([0, {}, "原文", { reason: "缺失" }]), ["原文", "缺失"]);
assert.equal(noonCaptureState(view, day).disabled, false);
assert.match(noonCaptureState(view, day).reason, /11:31至13:00/);
const beforeOpening = { ...view, can_capture: false, capture_window: {
  allowed: false, start_at: `${day}T11:31:00+08:00`, reason: '午评需在11:31至13:00前手动采集',
} };
assert.equal(noonCaptureState(beforeOpening, day).disabled, true);
assert.equal(noonCaptureState(beforeOpening, day).reason, beforeOpening.capture_window.reason);
assert.equal(noonCaptureState(view, "2026-10-27").disabled, true);
assert.equal(noonCaptureState({ ...view, today: "2026-10-29" }, day).disabled, true);
assert.equal(noonCaptureState({ ...view, can_capture: false }, day).disabled, true);
for (const state of ["queued", "running", "paused", "failed", "cancelled", "available", "purged"]) {
  assert.equal(noonCaptureState({ ...view, state }, day).disabled, true, `${state} never releases a submitted day`);
}
assert.equal(noonCaptureState({ ...view, locked_for_day: true }, day).disabled, true);
assert.equal(noonCaptureState(view, day, true).disabled, true);
assert.equal(noonShouldPoll({ ...view, state: "running" }, day), true);
assert.equal(noonShouldPoll({ ...view, state: "running" }, "2026-10-27"), false);
assert.equal(noonShouldPoll({ ...view, state: "paused" }, day), false);
assert.equal(noonCanResume({ ...view, state: "paused", task: { task_id: "fixture-job" } }, day), true);
assert.equal(noonCanResume({ ...view, state: "paused", capture_window: { allowed: false }, task: { task_id: "fixture-job" } }, day), false);
assert.equal(noonCanResume({ ...view, today: "2026-10-29", state: "paused", task: { task_id: "fixture-job" } }, day), false);
const saved = { ...view, state: "available", snapshot: { date: day, payload: { date: day, quote_as_of: null } } };
assert.equal(noonPayload(saved, day).quote_as_of, null);
assert.equal(noonPayload(saved, "2026-10-27"), null);
assert.equal(noonPayload({ ...saved, snapshot: { ...saved.snapshot, date: "2026-10-27" } }, day), null);
assert.equal(noonPayload({ ...saved, snapshot: { date: day, payload: { date: "2026-10-27" } } }, day), null);
assert.deepEqual(noonCalendarDates(["2026-10-27"], { today: day, items: [{ date: "2026-10-26", state: "purged" }, { date: "invalid" }] }), ["2026-10-26", "2026-10-27", day]);
assert.deepEqual(noonSortedRows([{ change_pct: null }, { change_pct: 0 }, { change_pct: -1 }, { change_pct: "99" }]).map((row) => row.change_pct), [0, -1, null, "99"]);
assert.equal(noonClock(null), "未提供");
assert.equal(noonClock(0), "未提供");
assert.equal(noonClock(-1), "未提供");
assert.deepEqual(noonCalendarDates([], { today: "2026-02-30" }), []);
assert.equal(noonChartDate(["2026-10-29", "2026-10-28", "2026-10-27"], day), "2026-10-27");
assert.equal(noonChartDate(["2026-10-29", "2026-10-28"], day), null);
assert.equal(noonChartDate(["2026-10-27", "2026-10-23"], "2026-10-26"), "2026-10-23");
const noonSector = { instrument_id: "884288.TI", topic_code: "884288.TI", name: "合成行业", kind: "industry" };
const sectorTarget = noonSectorSelection(noonSector, day, ["2026-10-29", day, "2026-10-27"]);
assert.equal(sectorTarget.topic.topic_code, "884288.TI");
assert.equal(sectorTarget.date, "2026-10-27", "noon board detail must stop before the captured day even after newer publication");
assert.equal(sectorTarget.view, "members");
assert.equal(noonSectorSelection(noonSector, day, ["2026-10-27"], "chart").view, "chart");
assert.equal(sectorTarget.topic.noon_snapshot_date, day);
assert.equal(sectorTarget.topic.noon_chart_as_of, "2026-10-27");
assert.equal(noonSectorSelection(noonSector, day, [day, "2026-10-29"]), null);
assert.equal(noonSectorSelection({ ...noonSector, topic_code: "885001.TI" }, day, ["2026-10-27"]), null, "conflicting source identities must not open a same-name substitute");
assert.equal(noonSectorSelection({ name: "同名无代码", kind: "industry" }, day, ["2026-10-27"]), null);
assert.equal(noonSectorSelection(noonSector, "2026-02-30", ["2026-02-27"]), null);
assert.equal(noonSourceCaption({ name: "stocks_page_1" }), "全市场个股 · 第1页");
assert.equal(noonSourceCaption({ name: "sectors_7" }), "板块指数 · 第7批");
assert.equal(noonSourceCaption({ name: "catalog_concept" }), "概念目录");
assert.match(noonClock("2026-10-28T11:30:00+08:00"), /11:30:00/);
assert.deepEqual(noonPreviousText({ summary: ["昨天保存原文"], payload: { summary: "不能替换原文" } }), ["昨天保存原文"]);
assert.equal(noonPreviousFacts({ date: "2026-10-27", state: "background_only", payload: { date: "2026-10-27", mode: "previous_session_price_facts", up: 2 } }).up, 2);
assert.equal(noonPreviousFacts({ date: "2026-10-27", state: "background_only", payload: { date: "2026-10-26", mode: "previous_session_price_facts", up: 2 } }), null);
const app = await fs.readFile(new URL("../../App.tsx", import.meta.url), "utf8");
assert.match(app, /key !== "noon" && key !== "auction" && date && !dates\.includes\(date\)/);
assert.match(app, /marketDates=\{dates\}/, "noon-only dates must not expand backtest inputs");
console.log("noon presentation: daily lock, unknown data, date isolation, frozen context passed");
