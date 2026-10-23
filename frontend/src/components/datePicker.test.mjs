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
  datePickerModel,
  datePickerSelectable,
  validateDatePickerChoice,
  moveDatePickerFocus,
  monthForYear,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const model = datePickerModel({
  value: "2026-10-21",
  min: "2016-10-21",
  max: "2026-10-21",
});
assert.equal(model.months.length, 121);
assert.equal(
  monthForYear(model.months, "2016", "2026-10"),
  "2016-10",
  "ten years can be reached directly by year selection",
);
assert.equal(datePickerSelectable("2016-10-20", model), false);
assert.equal(datePickerSelectable("2026-10-22", model), false);
assert.equal(
  datePickerSelectable("2026-10-17", model),
  true,
  "a bounded calendar alone does not invent a trading-day restriction",
);
assert.equal(validateDatePickerChoice("2026-02-30", model).date, null);
assert.equal(validateDatePickerChoice("2023-02-29", model).date, null);
assert.equal(validateDatePickerChoice("2024-02-29", model).date, "2024-02-29");
assert.equal(
  validateDatePickerChoice(" 2026-10-21 ", model).date,
  "2026-10-21",
);
assert.equal(validateDatePickerChoice("2026-10-22", model).date, null);
const discrete = datePickerModel({
  value: "2026-10-19",
  min: "2026-10-01",
  max: "2026-10-21",
  allowedDates: [
    "2026-10-21",
    "2026-10-16",
    "2026-10-19",
    "2026-10-19",
    "bad",
    "2026-10-22",
  ],
});
assert.deepEqual(discrete.allowedDates, [
  "2026-10-16",
  "2026-10-19",
  "2026-10-21",
]);
assert.equal(
  datePickerModel({
    value: "",
    allowedDates: discrete.allowedDates,
    fallbackDate: "2000-01-01",
  }).initialDate,
  "2026-10-21",
  "an empty retained-market selection opens latest data independently of the computer date",
);
assert.equal(datePickerSelectable("2026-10-17", discrete), false);
assert.equal(validateDatePickerChoice("2026-10-20", discrete).date, null);
assert.equal(
  moveDatePickerFocus("2026-10-16", "ArrowRight", discrete),
  "2026-10-19",
);
assert.equal(
  moveDatePickerFocus("2026-10-19", "ArrowLeft", discrete),
  "2026-10-16",
);
assert.equal(
  moveDatePickerFocus("2026-10-21", "ArrowRight", discrete),
  "2026-10-21",
);
assert.equal(
  moveDatePickerFocus("2026-10-16", "ArrowLeft", discrete),
  "2026-10-16",
);
assert.equal(moveDatePickerFocus("2026-10-21", "Home", discrete), "2026-10-19");
assert.equal(moveDatePickerFocus("2026-10-16", "End", discrete), "2026-10-16");
assert.equal(
  moveDatePickerFocus("2024-01-31", "PageDown", model),
  "2024-02-29",
);
assert.equal(moveDatePickerFocus("2024-03-31", "PageUp", model), "2024-02-29");
assert.equal(
  moveDatePickerFocus("2024-02-29", "PageDown", model, true),
  "2025-02-28",
);
assert.equal(moveDatePickerFocus("2026-10-21", "ArrowUp", model), "2026-10-14");
assert.equal(
  moveDatePickerFocus("2026-10-21", "ArrowDown", model),
  "2026-10-21",
);
assert.equal(
  moveDatePickerFocus("2026-10-21", "Enter", model),
  null,
  "Enter retains native button activation rather than moving focus",
);
assert.deepEqual(datePickerModel({ value: "", allowedDates: [] }).months, []);
assert.equal(
  validateDatePickerChoice(
    "2026-10-21",
    datePickerModel({ value: "", allowedDates: [] }),
  ).date,
  null,
);
assert.deepEqual(
  datePickerModel({ value: "2026-10-21", min: "2026-10-22", max: "2026-10-20" })
    .months,
  [],
);
const component = await fs.readFile(
  new URL("./DatePicker.tsx", import.meta.url),
  "utf8",
);
assert.match(
  component,
  /window.addEventListener\("keydown", escape, true\)/,
  "Escape is handled before enclosing modal document capture",
);
assert.match(component, /event.stopImmediatePropagation\(\)/);
assert.match(component, /tabIndex=\{cell.date === focused \? 0 : -1\}/);
assert.match(component, /moveDatePickerFocus\(/);
assert.equal(component.includes('type="date"'), false);
for (const file of [
  "../features/backtests/Backtests.tsx",
  "../features/sector/SectorMembersDialog.tsx",
]) {
  const page = await fs.readFile(new URL(file, import.meta.url), "utf8");
  assert.equal(page.includes('type="date"'), false);
  assert.match(page, /<DatePicker/);
}
console.log(
  "Shared date picker: real dates, range/discrete scope, decade/year jumps, leap dates, keyboard movement and nested modal Escape passed",
);
