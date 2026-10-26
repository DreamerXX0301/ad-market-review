/** Controlled hooks prove lazy GETs, bounded cache reuse and stale-response isolation. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const states = [],
  refs = [],
  callbacks = [],
  effects = [],
  requested = [];
let cursor = 0,
  scheduled = [];
const listeners = new Map();
globalThis.window = {
  addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
  removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
  dispatchEvent(event) { for (const listener of listeners.get(event.type) || []) listener(event); },
};
const same = (a, b) =>
  a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
globalThis.__savedRunHooks = {
  useState(initial) {
    const index = cursor++;
    if (!(index in states))
      states[index] = typeof initial === "function" ? initial() : initial;
    return [
      states[index],
      (value) => {
        states[index] =
          typeof value === "function" ? value(states[index]) : value;
      },
    ];
  },
  useRef(initial) {
    const index = cursor++;
    return (refs[index] ||= { current: initial });
  },
  useCallback(callback, deps) {
    const index = cursor++;
    if (!callbacks[index] || !same(callbacks[index].deps, deps))
      callbacks[index] = { callback, deps };
    return callbacks[index].callback;
  },
  useEffect(effect, deps) {
    const index = cursor++;
    if (!effects[index] || !same(effects[index].deps, deps))
      scheduled.push(() => {
        effects[index]?.cleanup?.();
        effects[index] = { deps, cleanup: effect() };
      });
  },
};
globalThis.__savedRunApi = (path, options) =>
  new Promise((resolve, reject) =>
    requested.push({ path, options, resolve, reject, answered: false }),
  );
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
const react = encode(
  "export const {useState,useEffect,useRef,useCallback}=globalThis.__savedRunHooks;",
);
const api = encode("export const api=globalThis.__savedRunApi;");
const helpers = encode(
  compile(
    await fs.readFile(new URL("./savedRunCache.ts", import.meta.url), "utf8"),
  ),
);
let compiled = compile(
  await fs.readFile(
    new URL("./useSavedStrategyRun.ts", import.meta.url),
    "utf8",
  ),
);
compiled = compiled
  .replaceAll('"react"', JSON.stringify(react))
  .replaceAll('"../../lib/api"', JSON.stringify(api))
  .replaceAll('"./savedRunCache"', JSON.stringify(helpers));
const { default: useSavedRun } = await import(encode(compiled));
const a = { strategy_id: "a", version: 7, latest_run: { run_id: "run-a" } },
  b = { strategy_id: "b", version: 1, latest_run: { run_id: "run-b" } };
const view = {
  strategy: a,
  target: "stock",
  decision: "matched",
  input: "2026-10-20",
};
function render(patch = {}) {
  Object.assign(view, patch);
  cursor = 0;
  const result = useSavedRun(
    view.target,
    view.strategy,
    view.decision,
    view.input,
  );
  const pending = scheduled;
  scheduled = [];
  pending.forEach((effect) => effect());
  return result;
}
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};
async function tick() {
  await flush();
  render();
  await flush();
  return render();
}
function pending(path) {
  const request = requested.find(
    (item) =>
      item.path === path && !item.answered && !item.options.signal.aborted,
  );
  assert.ok(request, `missing GET ${path}`);
  request.answered = true;
  return request;
}
async function respond(path, value) {
  pending(path).resolve(value);
  return tick();
}
const row = (decision, id) => ({ decision, instrument_id: id });
const run = (id, decision = "matched", rows = [row(decision, id)]) => ({
  strategy_id: view.strategy.strategy_id,
  run_id: id,
  business_date: "2026-10-20",
  strategy_version: 7,
  matched_count: 59,
  unknown_count: 412,
  unmatched_count: 5107,
  items: rows,
});
render();
assert.equal(requested[0].path, "/strategies/a");
assert.equal(
  requested.length,
  1,
  "a small metadata GET precedes saved-cache use",
);
await respond("/strategies/a", a);
assert.equal(requested[1].path, "/strategy-runs/run-a?decision=matched");
await respond(
  "/strategy-runs/run-a?decision=matched",
  run("run-a", "matched", [
    row("matched", "one"),
    row("unknown", "must-not-retain"),
  ]),
);
assert.equal(render().run.items.length, 1);
assert.equal(render().run.matched_count, 59);
assert.equal(render().candidatesReady, true);
render({ decision: "unknown" });
assert.equal(
  render().candidatesReady,
  false,
  "other-decision rows cannot flash in the new tab",
);
const unknownRows = Array.from({ length: 75 }, (_, index) =>
  row("unknown", String(index)),
);
await respond(
  "/strategy-runs/run-a?decision=unknown",
  run("run-a", "unknown", unknownRows),
);
assert.equal(
  render().run.items.length,
  75,
  "entire decision remains available for full sorting and navigation",
);
const reads = requested.length;
render({ decision: "matched" });
assert.equal(render().run.items[0].instrument_id, "one");
assert.equal(
  requested.length,
  reads,
  "returning to a cached decision needs no large GET",
);
render({ decision: "unmatched" });
const departedTab = pending("/strategy-runs/run-a?decision=unmatched");
render({ decision: "unknown" });
assert.equal(departedTab.options.signal.aborted, true);
departedTab.resolve(run("run-a", "unmatched"));
await tick();
assert.equal(render().run.loaded_decision, "unknown");
assert.equal(render({ strategy: b }).run, null);
const departedStrategy = pending("/strategies/b");
render({ strategy: a });
assert.equal(departedStrategy.options.signal.aborted, true);
await respond("/strategies/a", a);
assert.equal(render().run.run_id, "run-a");
departedStrategy.resolve(b);
await tick();
assert.equal(render().run.strategy_id, "a");
render({ decision: "matched" });
const updated = { ...a, latest_run: { run_id: "run-a-latest" } };
render({ strategy: updated });
assert.equal(
  render().run.run_id,
  "run-a",
  "last successful metadata survives a new-result load",
);
assert.equal(render().candidatesReady, false);
await respond("/strategies/a", updated);
pending("/strategy-runs/run-a-latest?decision=matched").reject(
  Error("fixture unavailable"),
);
await tick();
assert.match(render().error, /fixture unavailable/);
assert.equal(render().run.run_id, "run-a");
render().retry();
render();
await respond("/strategies/a", updated);
await respond(
  "/strategy-runs/run-a-latest?decision=matched",
  run("run-a-latest"),
);
assert.equal(render().run.run_id, "run-a-latest");
render().loadRun("old-history");
render();
await respond(
  "/strategy-runs/old-history?decision=matched",
  run("old-history"),
);
assert.equal(render().run.run_id, "old-history");
render().setRun(
  run("new-execution", "matched", [
    row("matched", "new"),
    row("unmatched", "drop-large-part"),
  ]),
);
await tick();
assert.equal(render().run.run_id, "new-execution");
assert.deepEqual(
  render().run.items.map((item) => item.instrument_id),
  ["new"],
);
render({ decision: "unknown" });
await respond(
  "/strategy-runs/new-execution?decision=unknown",
  run("new-execution", "unknown"),
);
render({ decision: "matched" });
assert.deepEqual(
  render().run.items.map((item) => item.instrument_id),
  ["new"],
);
render().setRun({ strategy_id: "b", run_id: "wrong-late-result", items: [] });
assert.equal(render().run.run_id, "new-execution");
const sector = {
  strategy_id: "a",
  version: 1,
  latest_run: { run_id: "sector-saved" },
};
render({ strategy: sector, target: "sector" });
await respond("/sector-strategies/a", sector);
await respond(
  "/sector-strategy-runs/sector-saved?decision=matched",
  run("sector-saved"),
);
assert.equal(render().run.run_id, "sector-saved");
render({ input: "2026-10-21" });
assert.equal(
  render().candidatesReady,
  false,
  "new input scope must be revalidated",
);
await respond("/sector-strategies/a", sector);
await respond(
  "/sector-strategy-runs/sector-saved?decision=matched",
  run("sector-saved"),
);
render({ strategy: undefined });
render({ strategy: sector });
pending("/sector-strategies/a").reject(
  Object.assign(Error("deleted"), { status: 404 }),
);
await tick();
assert.equal(
  render().run,
  null,
  "deleted strategy cannot be restored from memory cache",
);
assert.match(render().error, /deleted/);
render().retry();
render();
await respond("/sector-strategies/a", sector);
assert.ok(
  requested.find(
    (item) =>
      item.path === "/sector-strategy-runs/sector-saved?decision=matched" &&
      !item.answered,
  ),
);
pending("/sector-strategy-runs/sector-saved?decision=matched").reject(
  Object.assign(Error("increment locked"), { status: 409, incrementLocked: true }),
);
await tick();
assert.match(render().error, /increment locked/);
const lockedReads = requested.length;
window.dispatchEvent(new Event("workbench-increment-unlocked")); render();
assert.equal(requested.length, lockedReads + 1, "confirmed unlock retries the failed saved-result GET once");
assert.equal(requested.at(-1).path, "/sector-strategies/a");
window.dispatchEvent(new Event("workbench-increment-unlocked")); render();
assert.equal(requested.length, lockedReads + 1, "a repeated unlock cannot refetch an already consumed failure");
await respond("/sector-strategies/a", sector);
await respond("/sector-strategy-runs/sector-saved?decision=matched", run("sector-saved"));
assert.equal(render().error, "");
assert.equal(
  requested.every((request) => !request.options.method),
  true,
  "saved-run browsing never POSTs a selection",
);
assert.equal(
  requested
    .filter((item) => /strategy-runs\//.test(item.path))
    .every((item) => /\?decision=(matched|unknown|unmatched)$/.test(item.path)),
  true,
);
effects.forEach((effect) => effect?.cleanup?.());
delete globalThis.window;
console.log(
  "Saved run lazy decisions: GET-only validation, complete decision rows, memory reuse, stale-tab/strategy abort, retry, history/new result and sector isolation passed",
);
