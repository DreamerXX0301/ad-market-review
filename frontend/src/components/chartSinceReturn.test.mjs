import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./chartSinceReturn.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { chartSinceReturn: since } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const start = {
  date: "2026-10-09",
  close: 12.9,
  corporate_action_coverage_known: true,
};
const end = {
  date: "2026-10-21",
  close: 13.5,
  corporate_action_coverage_known: true,
};
const data = {
  as_of: end.date,
  instrument: { kind: "stock" },
  price_basis: "none",
  bars: [start, end],
  adjustment: { events: [], usable: true },
};
const before = structuredClone(data);
assert.ok(Math.abs(since(start, data).value - (13.5 / 12.9 - 1) * 100) < 1e-10);
assert.equal(since(end, data).value, 0);
assert.match(since(start, data).reason, /2026-10-09收盘至2026-10-21收盘/);
assert.equal(
  since(start, { ...data, as_of: "2026-10-22" }).value,
  null,
  "never replace a missing observation-date close with stale data",
);
assert.equal(since(start, { ...data, bars: [start, end, end] }).value, null);
assert.equal(since({ ...start, close: 0 }, data).value, null);
assert.equal(since({ ...start, close: "12.9" }, data).value, null);
assert.equal(since({ ...start, date: "2026-10-22" }, data).value, null);
assert.equal(since({ ...start, date: "2026-02-30" }, data).value, null);
assert.equal(
  since(start, {
    ...data,
    bars: [start, { ...end, corporate_action_coverage_known: false }],
  }).value,
  null,
);
assert.equal(
  since(start, { ...data, bars: [start, { ...end, corporate_action: true }] })
    .value,
  null,
);
const action = { date: "2026-10-20", factor: 0.5 };
assert.equal(
  since(start, {
    ...data,
    adjustment: { usable: true, anchor: end.date, events: [action] },
  }).value,
  null,
  "raw endpoints cannot cross a known action",
);
assert.notEqual(
  since(start, {
    ...data,
    adjustment: { usable: true, events: [{ date: "2026-10-22" }] },
  }).value,
  null,
  "future actions do not enter the selected window",
);
for (const actionDate of ["2026-10-20", end.date, null, "2026-02-30"])
  assert.equal(
    since(start, {
      ...data,
      adjustment: {
        usable: false,
        events: [],
        quarantined: [{ date: actionDate, reason: "除权日缺报价" }],
      },
    }).value,
    null,
    "a quarantined action must not disappear just because its date has no quote",
  );
for (const actionDate of [start.date, "2026-09-30", "2026-10-22"])
  assert.notEqual(
    since(start, {
      ...data,
      adjustment: {
        usable: false,
        events: [],
        quarantined: [{ date: actionDate }],
      },
    }).value,
    null,
    "a dated action outside the endpoint interval cannot alter the comparison",
  );
assert.notEqual(
  since(start, {
    ...data,
    price_basis: "forward_event_affine",
    adjustment: { usable: true, anchor: end.date, events: [action] },
  }).value,
  null,
);
assert.equal(
  since(start, {
    ...data,
    price_basis: "forward_event_affine",
    adjustment: { usable: false },
  }).value,
  null,
);
assert.equal(
  since(start, {
    ...data,
    price_basis: "forward_event_affine",
    adjustment: { usable: true, anchor: "2026-10-22", events: [] },
  }).value,
  null,
  "never use a future adjustment anchor",
);
assert.equal(
  since(start, { ...data, instrument: { kind: "unknown" } }).value,
  null,
);
assert.equal(
  since(start, { ...data, price_basis: "provider_unknown" }).value,
  null,
);
assert.notEqual(
  since(start, {
    ...data,
    instrument: { kind: "index" },
    bars: [start, { ...end, corporate_action_coverage_known: undefined }],
  }).value,
  null,
);
assert.match(since({ ...start, month_closed: false }, data).reason, /形成中/);
assert.deepEqual(data, before);
console.log(
  "chart since-return: frozen observation, same-basis endpoints, action guards and no stale close passed",
);
