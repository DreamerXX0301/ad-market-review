import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const code = ts.transpileModule(
  await fs.readFile(
    new URL("./intradayChartNavigation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { intradayChartSelection } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const day = "2026-11-05",
  previous = "2026-11-04";
for (const slot of ["auction", "open_30m", "noon", "afternoon_30m", "close"]) {
  assert.deepEqual(
    intradayChartSelection(day, [previous, day], previous, {
      slot,
      snapshotId: "intraday_old",
    }),
    { asOf: day },
  );
  assert.deepEqual(
    intradayChartSelection(day, [previous], previous, {
      slot,
      closingDate: day,
      snapshotId: "intraday_old",
    }),
    { asOf: day },
  );
}
assert.deepEqual(
  intradayChartSelection(day, [previous], previous, {
    slot: "noon",
    snapshotId: "noon_original",
  }),
  { asOf: previous, noonSnapshotId: "noon_original" },
);
assert.deepEqual(
  intradayChartSelection(day, [previous], previous, {
    slot: "open_30m",
    snapshotId: "intraday_original",
  }),
  { asOf: previous, intradaySnapshotId: "intraday_original" },
);
assert.deepEqual(
  intradayChartSelection(day, [previous, "2026-11-06"], previous, {
    slot: "auction",
    closingDate: "2026-11-06",
  }),
  { asOf: previous },
);
assert.equal(intradayChartSelection(day, [], day, { slot: "noon" }), null);
assert.equal(
  intradayChartSelection(day, [], "2026-11-06", { slot: "noon" }),
  null,
);
assert.equal(
  intradayChartSelection("2026-02-30", ["2026-02-30"], previous),
  null,
);
assert.equal(intradayChartSelection(day, [], null), null);
console.log("盘中图表按同日正式发布回看、跨日与临时快照隔离通过");
