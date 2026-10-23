import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./ladderFeedbackSummary.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { summarizeLadderFeedback } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

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
  [2, 3, 4, null],
);
assert.equal(summary.heights[0].average, -2.5);
assert.equal(summary.heights[1].average, 8);
assert.equal(summary.heights[1].unknown, 1);
assert.equal(summary.heights[2].average, null);
assert.equal(summary.heights[2].median, null);
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
console.log(
  "Ladder feedback: finite returned samples only; missing values stay unknown and sorting does not affect totals",
);
