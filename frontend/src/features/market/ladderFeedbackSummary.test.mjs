import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
async function moduleUrl(path) {
  let compiled = ts.transpileModule(await fs.readFile(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  for (const match of [...compiled.matchAll(/from (["'])(\.[^"']+)\1/g)])
    compiled = compiled.replace(match[0], "from " + JSON.stringify(await moduleUrl(new URL(match[2] + ".ts", path))));
  return encode(compiled);
}
const { summarizeLadderFeedback } = await import(await moduleUrl(new URL("./ladderFeedbackSummary.ts", import.meta.url)));
const { previousBoardHeight } = await import(await moduleUrl(new URL("./boardPresentation.ts", import.meta.url)));
const { sortMarketRows } = await import(await moduleUrl(new URL("./marketPresentation.ts", import.meta.url)));

const rows = [
  { change_pct: -5, previous_board_count: 2 },
  { change_pct: 0, previous_board_count: 2 },
  { change_pct: 8, previous_board_count: 3 },
  { change_pct: null, previous_board_count: 3 },
  { change_pct: "9", previous_board_count: 4 },
  { change_pct: Number.NaN, previous_board_count: 4 },
  { change_pct: Number.POSITIVE_INFINITY, previous_board_count: 4 },
  { change_pct: -1, previous_board_count: null },
];
const summary = summarizeLadderFeedback(rows);
assert.deepEqual(
  { ...summary, heights: undefined },
  {
    total: 8,
    known: 4,
    unknown: 4,
    up: 1,
    down: 2,
    flat: 1,
    sharpDecline: 1,
    average: 0.5,
    median: -0.5,
    heights: undefined,
  },
);
assert.deepEqual(
  summary.heights.map((group) => group.height),
  [4, 3, 2, null],
);
assert.equal(summary.heights[2].average, -2.5);
assert.equal(summary.heights[1].average, 8);
assert.equal(summary.heights[1].unknown, 1);
assert.equal(summary.heights[0].average, null);
assert.equal(summary.heights[0].median, null);
assert.equal(summary.heights[3].average, -1);
assert.deepEqual(
  rows.map((row) => row.change_pct),
  [-5, 0, 8, null, "9", Number.NaN, Number.POSITIVE_INFINITY, -1],
);
assert.deepEqual(summarizeLadderFeedback([...rows].reverse()), summary);
assert.equal(summarizeLadderFeedback([{ change_pct: 3 }]).median, 3);
assert.equal(summarizeLadderFeedback([{ change_pct: null }]).average, null);
assert.deepEqual(summarizeLadderFeedback([]), {
  total: 0,
  known: 0,
  unknown: 0,
  up: 0,
  down: 0,
  flat: 0,
  sharpDecline: 0,
  average: null,
  median: null,
  heights: [],
});
const day = "2026-01-05", previous = "2026-01-02", snapshot = "synthetic-generation";
const confirmed = {
  instrument_id: "600001.SH", date: day, snapshot_id: snapshot,
  previous_board_date: previous, previous_board_count: 99, change_pct: 1,
  board_presentation: {
    version: "dated-stock-board-presentation-v1", date: day, snapshot_id: snapshot, instrument_id: "600001.SH",
    previous_height: { value: 2, label: "二板", state: "confirmed", source_date: previous },
  },
};
assert.equal(previousBoardHeight(confirmed, day, snapshot), 2, "confirmed new evidence controls grouping and sorting despite conflicting legacy raw height");
const invalidRows = [];
for (const mutate of [
  (value) => { value.board_presentation.previous_height.value = 2.5; },
  (value) => { value.board_presentation.previous_height.value = 0; },
  (value) => { value.board_presentation.previous_height.state = "unknown"; },
  (value) => { value.board_presentation.previous_height.source_date = "2025-12-31"; },
  (value) => { value.board_presentation.previous_height.source_date = day; },
  (value) => { value.previous_board_date = "2026-02-30"; },
  (value) => { delete value.previous_board_date; },
  (value) => { value.board_presentation.date = previous; },
  (value) => { value.board_presentation.snapshot_id = "other-generation"; },
  (value) => { value.board_presentation.instrument_id = "600002.SH"; },
  (value) => { value.snapshot_id = "other-generation"; },
  (value) => { value.date = previous; },
  (value) => { value.evidence_date = previous; },
  (value) => { value.board_presentation = null; },
  (value) => { value.board_presentation = {}; },
  (value) => { value.board_presentation = undefined; },
]) {
  const invalid = structuredClone(confirmed); mutate(invalid); invalidRows.push(invalid);
  assert.equal(previousBoardHeight(invalid, day, snapshot), null, "a present new payload with invalid/unknown dated evidence cannot fall back to a large raw height");
}
for (const raw of [0, -1, 2.5, "3", Number.NaN, Number.POSITIVE_INFINITY])
  assert.equal(previousBoardHeight({ previous_board_count: raw }, day, snapshot), null);
assert.equal(previousBoardHeight({ previous_board_count: 3 }, day, snapshot), 3, "a genuinely absent new object retains the legacy positive-integer compatibility path");
const feedbackRows = [...invalidRows, confirmed, { previous_board_count: 3, change_pct: -1 }];
const unchanged = structuredClone(feedbackRows);
const mapped = feedbackRows.map((row) => ({ ...row, feedback_height: previousBoardHeight(row, day, snapshot) }));
const sorted = sortMarketRows(mapped, { key: "feedback_height", order: "desc" });
assert.deepEqual(sorted.map((row) => row.feedback_height), [3, 2, ...invalidRows.map(() => null)], "unknown evidence stays after confirmed integer heights in the same table sorter");
assert.deepEqual(summarizeLadderFeedback(feedbackRows, day, snapshot).heights.map((group) => [group.height, group.total]), [[3, 1], [2, 1], [null, invalidRows.length]], "summary and table share one confirmed-height interpretation");
assert.deepEqual(feedbackRows, unchanged, "UI mapping must not overwrite raw payload heights");
const view = await fs.readFile(new URL("./LadderFeedback.tsx", import.meta.url), "utf8");
assert.match(view, /defaultSort=\{\{ key: "feedback_height", order: "desc" \}\}/);
assert.match(view, /rows=\{tableRows\}/);
assert.match(view, /summarizeLadderFeedback\(rows, date, snapshotId\)/);
console.log(
  "Ladder feedback: finite returns, shared confirmed dated heights, legacy compatibility, unknown-last sorting and unchanged source rows passed",
);
