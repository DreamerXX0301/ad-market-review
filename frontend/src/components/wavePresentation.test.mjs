import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./wavePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  wavePatterns,
  waveCandidates,
  selectedWavePatterns,
  waveTurningPoints,
  wavePlotSeries,
  wavePatternWindow,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

const dateAt = (offset) =>
  new Date(Date.UTC(2026, 0, 1 + offset)).toISOString().slice(0, 10);
function fixture(id, offset, direction, correction = true) {
  const prices = [100, 120, 110, 150, 130, 160, 140, 150, 120].slice(
    0,
    correction ? 9 : 6,
  );
  const points = prices.map((price, index) => ({
    date: dateAt(offset + index * 3),
    confirmed_at: dateAt(offset + index * 3 + 2),
    price: direction === "up" ? price : 300 - price,
    kind: (index % 2 === 0) === (direction === "up") ? "low" : "high",
    label:
      index === 0
        ? "0"
        : index < 6
          ? `${index}?`
          : ["A?", "B?", "C?"][index - 6],
    phase: index < 6 ? "impulse" : "correction",
  }));
  return {
    pattern_id: id,
    direction,
    label: "backend label",
    start_date: points[0].date,
    end_date: points.at(-1).date,
    confirmed_at: points.at(-1).confirmed_at,
    has_correction: correction,
    points,
    rules: ["价格骨架规则"],
    scope: "仅骨架",
  };
}
const oldUp = fixture("old-up", 0, "up", false),
  up = fixture("up", 30, "up"),
  down = fixture("down", 70, "down");
const sourceData = {
  patterns: [oldUp, up, down],
  points: up.points.map((point) => ({ ...point, label: "99?" })),
};
const before = structuredClone(sourceData);
const barDates = Array.from({ length: 120 }, (_, index) => dateAt(index));
const patterns = wavePatterns(sourceData, barDates, dateAt(119));
const locate = wavePatternWindow(up, barDates);
assert(
  locate.start <
    (barDates.indexOf(up.start_date) / (barDates.length - 1)) * 100,
);
assert(
  locate.end > (barDates.indexOf(up.end_date) / (barDates.length - 1)) * 100,
  "explicit location includes both ends with margin",
);
assert.equal(
  wavePatternWindow(oldUp, barDates).start,
  0,
  "location clamps to available chart boundary",
);
assert.equal(
  wavePatternWindow(
    up,
    barDates.filter((date) => date !== up.start_date),
  ),
  null,
  "missing endpoint cannot be located by guessing",
);
assert.equal(wavePatternWindow(up, []), null);
assert.deepEqual(
  patterns.map((pattern) => pattern.pattern_id),
  ["down", "up", "old-up"],
  "latest confirmed structure is selected first",
);
assert.equal(
  selectedWavePatterns(patterns, "latest", "latest")[0].pattern_id,
  "down",
);
assert.deepEqual(
  waveCandidates(patterns, "up").map((pattern) => pattern.pattern_id),
  ["up", "old-up"],
);
assert.deepEqual(
  waveCandidates(patterns, "down").map((pattern) => pattern.pattern_id),
  ["down"],
);
assert.equal(
  selectedWavePatterns(waveCandidates(patterns, "up"), "all", "up").length,
  2,
);
assert.equal(
  selectedWavePatterns(patterns, "old-up", "latest")[0].pattern_id,
  "old-up",
);
assert.equal(
  selectedWavePatterns(patterns, "missing-id", "latest")[0].pattern_id,
  "down",
);
assert.equal(
  selectedWavePatterns(patterns, "all", "latest").length,
  1,
  "default latest view never silently expands all structures",
);
assert.equal(waveCandidates(patterns, "turning").length, 0);
assert.equal(
  wavePatterns({ points: sourceData.points }, barDates, dateAt(119)).length,
  0,
  "generic turning points are not converted into numbered waves",
);
assert.equal(
  wavePatterns(sourceData, barDates, dateAt(80)).length,
  2,
  "future-confirmed structure is excluded",
);
assert.equal(
  wavePatterns(
    { patterns: [up] },
    barDates.filter((date) => date !== up.points[2].date),
    dateAt(119),
  ).length,
  0,
  "a partial visible skeleton is not relabelled as complete",
);
const malformed = structuredClone(up);
malformed.points[2].label = "A?";
assert.equal(
  wavePatterns({ patterns: [malformed] }, barDates, dateAt(119)).length,
  0,
);
const invalidPrice = structuredClone(up);
invalidPrice.points[2].price = null;
assert.equal(
  wavePatterns({ patterns: [invalidPrice] }, barDates, dateAt(119)).length,
  0,
);
const invalidDate = structuredClone(up);
invalidDate.points[2].date = "2026-02-30";
assert.equal(
  wavePatterns({ patterns: [invalidDate] }, barDates, dateAt(119)).length,
  0,
);

const palette = {
  up: "red",
  down: "green",
  correction: "purple",
  turning: "gray",
};
const plotted = wavePlotSeries(patterns.slice(0, 2), [], "latest", palette);
assert.equal(
  plotted.length,
  4,
  "each full candidate owns a separate impulse and correction series",
);
for (const pattern of patterns.slice(0, 2)) {
  const impulse = plotted.find(
    (series) => series.id === `wave-${pattern.pattern_id}-impulse`,
  );
  const correction = plotted.find(
    (series) => series.id === `wave-${pattern.pattern_id}-correction`,
  );
  assert.deepEqual(
    impulse.data.map((point) => point.name),
    ["0", "1?", "2?", "3?", "4?", "5?"],
    "downward impulses also use 1—5",
  );
  assert.deepEqual(
    correction.data.map((point) => point.name),
    ["5?", "A?", "B?", "C?"],
    "ABC begins at its own impulse endpoint",
  );
  assert.deepEqual(
    impulse.data.map((point) => point.value[0]),
    pattern.points.slice(0, 6).map((point) => point.date),
  );
  assert.deepEqual(
    correction.data.map((point) => point.value[0]),
    pattern.points.slice(5).map((point) => point.date),
  );
  assert.equal(impulse.lineStyle.type, "solid");
  assert.equal(correction.lineStyle.type, "dashed");
  assert.equal(
    impulse.lineStyle.color,
    pattern.direction === "up" ? "red" : "green",
  );
  assert.equal(correction.lineStyle.color, "purple");
}
const turns = waveTurningPoints(sourceData, barDates, dateAt(119));
assert.equal(turns.length, 9);
assert(
  turns.every((point) => ["高点", "低点"].includes(point.label)),
  "ordinary swings never inherit numeric or ABC labels",
);
const turningSeries = wavePlotSeries(patterns, turns, "turning", palette);
assert.equal(turningSeries.length, 1);
assert.equal(
  turningSeries[0].label.formatter({
    name: "高点",
    value: ["2026-10-09", 12.88],
  }),
  "高点\n12.88",
  "turning labels expose their own chart-basis price without changing evidence",
);
assert.equal(
  turningSeries[0].label.formatter({ name: "低点", value: ["2026-10-09", 0] }),
  "低点\n0.00",
);
const segmentedTurns = turns.map((point, index) => ({
  ...point,
  segment_id: index < 4 ? "before-gap" : "after-gap",
}));
const splitSeries = wavePlotSeries([], segmentedTurns, "turning", palette);
assert.equal(
  splitSeries.length,
  2,
  "ordinary turning lines break at the backend continuity boundary",
);
assert.deepEqual(
  splitSeries[0].data.map((point) => point.value[0]),
  segmentedTurns.slice(0, 4).map((point) => point.date),
);
assert.deepEqual(
  splitSeries[1].data.map((point) => point.value[0]),
  segmentedTurns.slice(4).map((point) => point.date),
);
const mixedSkeleton = structuredClone(up);
mixedSkeleton.points.forEach(
  (point, index) => (point.segment_id = index < 4 ? "before-gap" : "after-gap"),
);
assert.equal(
  wavePatterns({ patterns: [mixedSkeleton] }, barDates, dateAt(119)).length,
  0,
  "even numbered structures must share one continuous run",
);
assert(
  turningSeries[0].data.every((point) => ["高点", "低点"].includes(point.name)),
);
assert.deepEqual(
  sourceData,
  before,
  "selection and plotting leave API evidence intact",
);
assert.deepEqual(wavePatterns(null, [], ""), []);
assert.deepEqual(wavePlotSeries([], [], "latest", palette), []);
console.log("wave presentation cases passed");
