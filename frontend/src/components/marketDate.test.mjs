import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./marketDate.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  isCalendarDate,
  retainedMarketDates,
  availableMonths,
  adjacentMarketDate,
  adjacentMonth,
  monthForYear,
  calendarMonthCells,
  validateMarketDateJump,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

for (const day of ["2024-02-29", "2026-10-20", "2000-02-29"])
  assert.equal(isCalendarDate(day), true);
for (const day of [
  "2023-02-29",
  "1900-02-29",
  "2026-02-30",
  "2026-13-01",
  "2026-00-01",
  "2026-10-1",
  "2026/10/20",
  "",
  "2026-10-20T00:00:00Z",
])
  assert.equal(isCalendarDate(day), false, day);

const days = retainedMarketDates([
  "2026-10-20",
  "2026-10-16",
  "2026-10-19",
  "2026-10-19",
  "bad",
  "2026-02-30",
]);
assert.deepEqual(days, ["2026-10-16", "2026-10-19", "2026-10-20"]);
assert.equal(adjacentMarketDate(days, "2026-10-19", -1), "2026-10-16");
assert.equal(adjacentMarketDate(days, "2026-10-19", 1), "2026-10-20");
assert.equal(adjacentMarketDate(days, "2026-10-16", -1), null);
assert.equal(adjacentMarketDate(days, "2026-10-20", 1), null);
assert.equal(adjacentMarketDate(days, "2026-10-17", -1), null);
assert.equal(adjacentMarketDate([], "", 1), null);
assert.deepEqual(validateMarketDateJump(" 2026-10-19 ", days), {
  date: "2026-10-19",
  error: "",
});
assert.equal(validateMarketDateJump("2026-10-17", days).date, null);
assert.match(validateMarketDateJump("2026-10-17", days).error, /没有已留存/);
assert.match(validateMarketDateJump("2026-10-21", days).error, /区间/);
assert.match(validateMarketDateJump("2023-10-20", days).error, /区间/);
assert.match(validateMarketDateJump("2026-02-30", days).error, /YYYY-MM-DD/);
assert.match(validateMarketDateJump("2026-10-20", []).error, /初始化/);

const months = availableMonths([
  "2023-10-20",
  "2023-12-29",
  "2024-01-02",
  "2024-10-08",
  "2026-10-20",
]);
assert.deepEqual(months, [
  "2023-10",
  "2023-12",
  "2024-01",
  "2024-10",
  "2026-10",
]);
assert.equal(adjacentMonth(months, "2023-12", -1), "2023-10");
assert.equal(adjacentMonth(months, "2023-12", 1), "2024-01");
assert.equal(adjacentMonth(months, "2023-10", -1), null);
assert.equal(adjacentMonth(months, "2026-10", 1), null);
assert.equal(monthForYear(months, "2024", "2026-10"), "2024-10");
assert.equal(monthForYear(months, "2023", "2024-01"), "2023-10");
assert.equal(monthForYear(months, "2025", "2026-10"), null);

const october = calendarMonthCells("2026-10");
assert.equal(october.length, 42);
assert.equal(october[0].date, "2026-09-28");
assert.equal(october[0].inMonth, false);
assert.equal(october.filter((cell) => cell.inMonth).length, 31);
assert.equal(
  calendarMonthCells("2024-02").filter((cell) => cell.inMonth).length,
  29,
);
assert.equal(
  calendarMonthCells("2023-02").filter((cell) => cell.inMonth).length,
  28,
);
assert.deepEqual(calendarMonthCells("2026-13"), []);
assert.deepEqual(calendarMonthCells("bad"), []);
assert.equal(new Set(october.map((cell) => cell.date)).size, 42);
for (let index = 1; index < october.length; index++)
  assert.equal(
    Date.parse(october[index].date) - Date.parse(october[index - 1].date),
    86400000,
  );
const enabled = october.filter(
  (cell) => cell.inMonth && days.includes(cell.date),
);
assert.deepEqual(
  enabled.map((cell) => cell.date),
  days,
);

const picker = await fs.readFile(
  new URL("./MarketDatePicker.tsx", import.meta.url),
  "utf8",
);
const shell = await fs.readFile(
  new URL("./AppShell.tsx", import.meta.url),
  "utf8",
);
assert.match(
  shell,
  /isMarket \? \(\s*<MarketDatePicker value=\{date\} dates=\{dates\} onChange=\{onDate\}/,
);
assert.equal(
  shell.includes("dates.map("),
  false,
  "the shell must not render hundreds of date options",
);
assert.match(picker, /role="dialog"/);
assert.equal(
  picker.includes('aria-modal="true"'),
  false,
  "date popover is nonmodal",
);
assert.match(picker, /event.key === "Escape"/);
assert.match(picker, /document.addEventListener\("pointerdown", dismiss\)/);
assert.match(
  picker,
  /disabled=\{!cell.inMonth \|\| !allowed.has\(cell.date\)\}/,
);
assert.match(picker, /if \(selected !== value\) onChange\(selected\)/);
assert.equal(picker.includes("location.hash"), false);
assert.equal(
  picker.includes("new Date()"),
  false,
  "opening must not jump to computer's current date",
);
assert.equal(picker.includes("setInterval"), false);
console.log(
  "Market date helper, boundaries, grid and isolated scope checks passed.",
);
