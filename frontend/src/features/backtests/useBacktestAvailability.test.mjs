/** Exercise retained-page strategy removal and delayed availability requests with real hook code. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const slots = [];
let cursor = 0,
  scheduled = [];
const same = (a, b) =>
  a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
globalThis.__backtestSetupHooks = {
  useState(initial) {
    const i = cursor++;
    slots[i] ||= { value: typeof initial === "function" ? initial() : initial };
    return [
      slots[i].value,
      (value) => {
        slots[i].value =
          typeof value === "function" ? value(slots[i].value) : value;
      },
    ];
  },
  useRef(initial) {
    const i = cursor++;
    slots[i] ||= { value: { current: initial } };
    return slots[i].value;
  },
  useEffect(effect, deps) {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps)) {
      const previous = slots[i];
      slots[i] = { deps, cleanup: previous?.cleanup };
      scheduled.push(() => {
        slots[i].cleanup?.();
        slots[i].cleanup = effect();
      });
    }
  },
};
const requested = [];
globalThis.__backtestSetupApi = (path, options) =>
  new Promise((resolve, reject) =>
    requested.push({ path, options, resolve, reject }),
  );
const encoded = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const hooks = encoded(
  "export const {useState,useEffect,useRef}=globalThis.__backtestSetupHooks;",
);
const api = encoded("export const api=globalThis.__backtestSetupApi;");
let compiled = ts.transpileModule(
  await fs.readFile(
    new URL("./useBacktestAvailability.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
compiled = compiled
  .replaceAll('"react"', JSON.stringify(hooks))
  .replaceAll('"../../lib/api"', JSON.stringify(api));
const {
  default: useAvailability,
  backtestStrategyOptions,
  resolvedBacktestStrategyId,
} = await import(encoded(compiled));
const a = { strategy_id: "a", version: 1 },
  b = { strategy_id: "b", version: 2 };
const options = Object.freeze([
  Object.freeze(a),
  Object.freeze({ ...b }),
  Object.freeze({ strategy_id: "archived", archived: true }),
  Object.freeze({ strategy_id: "deleted", deleted: true }),
]);
assert.deepEqual(backtestStrategyOptions(options), [a, b]);
assert.equal(resolvedBacktestStrategyId(options, "b"), "b");
assert.equal(resolvedBacktestStrategyId(options, "deleted"), "a");
assert.equal(
  resolvedBacktestStrategyId([b], "a"),
  "b",
  "deleted selection falls back to the first valid strategy",
);
assert.equal(resolvedBacktestStrategyId([], "a"), "");
let input = { strategy: a, ready: false, dataVersion: "v1" };
function render(next = input) {
  input = next;
  cursor = 0;
  const result = useAvailability(input);
  const effects = scheduled;
  scheduled = [];
  effects.forEach((effect) => effect());
  return result;
}
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};
const bounds = (from = "2026-01-01", to = "2026-10-21") => ({
  available: true,
  min_date: from,
  max_date: to,
  default_start: "2026-06-01",
  default_end: to,
});
assert.equal(render().canRun, false);
assert.equal(
  requested.length,
  0,
  "loading/error strategy lists never check old strategies",
);
assert.equal(render({ ...input, ready: true }).canRun, false);
assert.equal(requested.length, 1);
assert.deepEqual(JSON.parse(requested[0].options.body), { strategy_id: "a" });
requested[0].resolve(bounds());
await flush();
let ready = render();
assert.equal(ready.canRun, true);
assert.equal(ready.start, "2026-06-01");
ready.setStart("2026-07-01");
ready.setEnd("2026-09-01");
const updating = render({ ...input, dataVersion: "v2" });
assert.equal(updating.availability, null);
assert.equal(updating.canRun, false, "old generation bounds never enable run");
requested[1].resolve(bounds());
await flush();
ready = render();
assert.equal(ready.start, "2026-07-01");
assert.equal(
  ready.end,
  "2026-09-01",
  "same strategy/version refresh keeps in-range custom dates",
);
render({ ...input, dataVersion: "v3" });
const old = requested[2];
const other = render({ ...input, strategy: b });
assert.equal(other.canRun, false);
assert.equal(old.options.signal.aborted, true);
old.resolve({ ...bounds(), default_start: "2020-01-01" });
await flush();
assert.equal(
  render().availability,
  null,
  "late departed strategy bounds cannot replace current selection",
);
requested[3].resolve({ ...bounds(), default_start: "2026-08-01" });
await flush();
ready = render();
assert.equal(
  ready.start,
  "2026-08-01",
  "a different strategy uses its own defaults",
);
assert.equal(ready.canRun, true);
const removed = render({ ...input, strategy: undefined });
assert.equal(removed.availability, null);
assert.equal(removed.canRun, false);
assert.equal(removed.error, "");
assert.equal(
  requested.length,
  4,
  "removed selection never issues an availability POST",
);
render({ ...input, strategy: a });
const failed = requested[4];
failed.reject(Error("deleted strategy 409"));
await flush();
assert.match(render().error, /409/);
assert.equal(render().canRun, false);
const loading = render({ ...input, ready: false });
assert.equal(loading.error, "");
assert.equal(
  loading.canRun,
  false,
  "authority refresh must not reuse success or an old failure",
);
render({ ...input, ready: true, strategy: b });
const latest = requested[5];
const clearing = render({ ...input, strategy: undefined });
assert.equal(clearing.canRun, false);
assert.equal(latest.options.signal.aborted, true);
latest.reject(Error("late removed strategy 409"));
await flush();
assert.equal(render().error, "");
assert.equal(render().availability, null);
render({ ...input, strategy: a, ready: true });
requested[6].resolve({ available: false, reason: "coverage unavailable" });
await flush();
assert.equal(render().canRun, false);
slots.forEach((slot) => slot?.cleanup?.());
console.log(
  "backtest availability: removed/archived selection fallback, authority readiness, scoped bounds/errors, late-response cancellation and same-strategy date preservation passed",
);
