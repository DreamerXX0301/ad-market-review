import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
let source = ts
  .transpileModule(
    await fs.readFile(new URL("./api.ts", import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    },
  )
  .outputText.replaceAll(
    '"react"',
    JSON.stringify(import.meta.resolve("react")),
  )
  .replaceAll(
    '"../components/incrementSession"',
    JSON.stringify(encode("export function restoreIncrementSession(){}")),
  );
const { api, decodeChartColumns } = await import(encode(source));
const requests = [];
globalThis.fetch = (url, options) =>
  new Promise((resolve) => requests.push({ url, options, resolve }));
const answer = (request, value, status = 200) =>
  request.resolve({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(value),
  });
const abort = new AbortController(),
  kept = new AbortController();
const first = api("/overview?date=2026-10-29", { signal: abort.signal });
const rejected = assert.rejects(first, { name: "AbortError" });
const second = api("/overview?date=2026-10-29", { signal: kept.signal });
assert.equal(requests.length, 1);
abort.abort();
await rejected;
assert.equal(
  requests[0].options.signal.aborted,
  false,
  "one reader cannot cancel another reader",
);
answer(requests[0], { rows: [1] });
assert.deepEqual(await second, { rows: [1] });

const a = api("/strategies"),
  b = api("/strategies");
assert.equal(requests.length, 2);
answer(requests[1], { items: [{ name: "original" }] });
const [left, right] = await Promise.all([a, b]);
left.items[0].name = "local mutation";
assert.equal(
  right.items[0].name,
  "original",
  "shared transport must not share mutable parsed values",
);
const fresh = api("/strategies");
assert.equal(requests.length, 3);
answer(requests[2], { items: [] });
await fresh;

const x = new AbortController(),
  y = new AbortController();
const gone1 = assert.rejects(api("/old", { signal: x.signal }), {
  name: "AbortError",
});
const gone2 = assert.rejects(api("/old", { signal: y.signal }), {
  name: "AbortError",
});
x.abort();
y.abort();
await Promise.all([gone1, gone2]);
assert.equal(requests[3].options.signal.aborted, true);
const again = api("/old");
assert.equal(requests.length, 5);
answer(requests[4], { new: true });
await again;
answer(requests[3], { abandoned: true });

const old = api("/policy");
const post = api("/policy", { method: "POST", body: "{}" });
const updated = api("/policy");
assert.equal(requests.length, 8);
answer(requests[5], { revision: 1 });
answer(requests[6], { revision: 2 });
answer(requests[7], { revision: 2 });
assert.deepEqual(await updated, { revision: 2 });
await Promise.all([old, post]);
const differentDates = [
  api("/overview?date=2026-10-28"),
  api("/overview?date=2026-10-29"),
];
assert.equal(requests.length, 10);
answer(requests[8], { day: 28 });
answer(requests[9], { day: 29 });
assert.notDeepEqual(...(await Promise.all(differentDates)));

const packed = {
  as_of: "2026-10-29",
  bars_columnar: {
    version: "chart-bars-columns-v1",
    count: 2,
    fields: ["close", "optional", "proof"],
    columns: [
      [-0, null],
      [null, null],
      [{ unknown: true }, []],
    ],
    missing: { optional: [1] },
  },
};
const decoded = decodeChartColumns(packed);
assert(Object.is(decoded.bars[0].close, -0));
assert.equal(decoded.bars[0].optional, null);
assert(!Object.hasOwn(decoded.bars[1], "optional"));
assert.deepEqual(decoded.bars[0].proof, { unknown: true });
for (const patch of [
  { count: -1 },
  { columns: [[]] },
  { version: "unsupported" },
  { missing: { close: [2] } },
])
  assert.throws(() =>
    decodeChartColumns({
      ...packed,
      bars_columnar: { ...packed.bars_columnar, ...patch },
    }),
  );
const chart = api("/instruments/600001.SH/chart?as_of=2026-10-29");
assert.match(requests[10].url, /&encoding=columns-v1$/);
answer(requests[10], packed);
assert.deepEqual(
  await chart,
  decodeChartColumns(JSON.parse(JSON.stringify(packed))),
);
console.log(
  "Concurrent GETs: shared transport, independent abort/data, mutation/date freshness and lossless chart columns passed",
);
