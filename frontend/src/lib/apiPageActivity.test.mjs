import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const state = [],
  refs = [],
  callbacks = [],
  effects = [],
  requests = [],
  listeners = new Map();
let now = 1000;
const savedPerformance = globalThis.performance;
globalThis.performance = { now: () => now };
let cursor = 0,
  pending = [],
  path = "/tracking-rounds?status=tracking";
const same = (a, b) =>
  a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
globalThis.__pageHooks = {
  createContext(value) {
    return { value };
  },
  useContext(context) {
    return context.value;
  },
  useState(initial) {
    const i = cursor++;
    if (!(i in state))
      state[i] = typeof initial === "function" ? initial() : initial;
    return [
      state[i],
      (value) => {
        state[i] = typeof value === "function" ? value(state[i]) : value;
      },
    ];
  },
  useRef(value) {
    const i = cursor++;
    return (refs[i] ||= { current: value });
  },
  useCallback(fn, deps) {
    const i = cursor++;
    if (!callbacks[i] || !same(callbacks[i].deps, deps))
      callbacks[i] = { fn, deps };
    return callbacks[i].fn;
  },
  useEffect(fn, deps) {
    const i = cursor++;
    if (!effects[i] || !same(effects[i].deps, deps))
      pending.push(() => {
        effects[i]?.cleanup?.();
        effects[i] = { deps, cleanup: fn() };
      });
  },
};
globalThis.window = {
  addEventListener(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
  },
  removeEventListener(type, fn) {
    listeners.get(type)?.delete(fn);
  },
  dispatchEvent(event) {
    for (const fn of listeners.get(event.type) || []) fn(event);
  },
};
globalThis.fetch = (url, options) =>
  new Promise((resolve) => requests.push({ url, options, resolve }));
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
let source = ts.transpileModule(
  await fs.readFile(new URL("./api.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
source = source
  .replaceAll(
    '"react"',
    JSON.stringify(
      encode(
        "export const {createContext,useContext,useState,useRef,useEffect,useCallback}=globalThis.__pageHooks;",
      ),
    ),
  )
  .replaceAll(
    '"../components/incrementSession"',
    JSON.stringify(encode("export function restoreIncrementSession(){}")),
  );
const {
  api,
  useApi,
  ApiPageActivity,
  notifyReadChanges,
  observeReadScopes,
  firstGainersReadPath,
  prefetchHistoricalReads,
} = await import(encode(source));
ApiPageActivity.value = true;
function render() {
  cursor = 0;
  const result = useApi(path);
  const work = pending;
  pending = [];
  work.forEach((fn) => fn());
  return result;
}
function event(type, detail, navigation = false) {
  window.dispatchEvent(Object.assign(new Event(type), { detail, navigation }));
}
async function answer(request, value, status = 200) {
  request.resolve({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(value),
  });
  await new Promise((resolve) => setImmediate(resolve));
  return render();
}

render();
assert.equal(requests.length, 1);
event("workbench-route", "tracking", true);
render();
assert.equal(
  requests.length,
  1,
  "navigation must not abort and restart the activation GET",
);
await answer(requests[0], { items: ["old"] });
assert.deepEqual(render().data, { items: ["old"] });

render().reload();
render();
const abandoned = requests.at(-1);
ApiPageActivity.value = false;
render();
assert.equal(
  abandoned.options.signal.aborted,
  true,
  "leaving a retained page cancels its own GET",
);
const hiddenCount = requests.length;
event("workbench-route", "tracking");
event("workbench-topics-changed");
render().reload();
render();
assert.equal(
  requests.length,
  hiddenCount,
  "hidden listeners and manual invalidation cannot start GETs",
);
path = "/tracking-rounds?status=all&strategy_id=next";
assert.equal(
  render().data,
  null,
  "new scope cannot show the previous response even while hidden",
);
await answer(abandoned, { items: ["late-old"] });
ApiPageActivity.value = true;
render();
assert.equal(
  requests.length,
  hiddenCount + 1,
  "activation always revalidates the current scope once",
);
assert.match(requests.at(-1).url, /strategy_id=next/);
assert.equal(render().data, null);
await answer(requests.at(-1), { items: ["next"] });
assert.deepEqual(render().data, { items: ["next"] });

path = "/strategies";
render();
await answer(requests.at(-1), { version: 1 });
const beforeTask = requests.length;
event("workbench-route", "strategy");
render();
assert.equal(
  requests.length,
  beforeTask + 1,
  "task completion still refreshes a visible strategy consumer",
);
await answer(requests.at(-1), { version: 2 });
path = "/topics";
render();
await answer(requests.at(-1), { revision: 1 });
event("workbench-topics-changed");
render();
await answer(requests.at(-1), { revision: 2 });
assert.deepEqual(render().data, { revision: 2 }, "visible edits remain fresh");

path = "/data/status";
render();
await answer(
  requests.at(-1),
  { detail: { message: "locked", increment_session: {} } },
  409,
);
ApiPageActivity.value = false;
render();
const beforeUnlock = requests.length;
event("workbench-increment-unlocked");
render();
assert.equal(requests.length, beforeUnlock);
ApiPageActivity.value = true;
render();
assert.equal(
  requests.length,
  beforeUnlock + 1,
  "a hidden lock-failed page rereads on return without replaying a mutation",
);
await answer(requests.at(-1), { date: "2026-10-27" });
path = "/market/emotion?date=2026-10-26";
render();
await answer(requests.at(-1), { date: "2026-10-26" });
path = "/market/emotion?date=2026-10-27";
assert.equal(
  render().data,
  null,
  "day changes never expose the old date for one frame",
);
await answer(requests.at(-1), { date: "2026-10-27" });

path = "/market/ladder?date=2026-10-27";
render();
const knownLadder = { date: "2026-10-27", total: 23 };
await answer(requests.at(-1), knownLadder);
const beforeTabSwitch = requests.length;
ApiPageActivity.value = false;
render();
ApiPageActivity.value = true;
render();
assert.equal(requests.length, beforeTabSwitch, "quick tab return reuses the same saved ladder");
assert.deepEqual(render().data, knownLadder);
assert.equal(render().loading, false);
ApiPageActivity.value = false;
render();
now += 61000;
ApiPageActivity.value = true;
render();
assert.equal(requests.length, beforeTabSwitch + 1);
assert.deepEqual(render().data, knownLadder, "background check keeps the same-date view on screen");
assert.equal(render().loading, false);
await answer(requests.at(-1), { ...knownLadder, total: 24 });
assert.equal(render().data.total, 24);
ApiPageActivity.value = false;
render();
notifyReadChanges([{ scope: "market" }]);
render();
assert.equal(requests.length, beforeTabSwitch + 1, "hidden published reader invalidates without fetching");
ApiPageActivity.value = true;
render();
assert.equal(requests.length, beforeTabSwitch + 2, "publication bypasses the recent-read window");
await answer(requests.at(-1), knownLadder);
const beforeExplicit = requests.length;
render().reload();
render();
assert.equal(requests.length, beforeExplicit + 1, "manual refresh bypasses reuse");
await answer(requests.at(-1), knownLadder);

observeReadScopes(["market"]);
const beforeUnchanged = requests.length;
ApiPageActivity.value = false;
render();
now += 300000;
ApiPageActivity.value = true;
render();
assert.equal(requests.length, beforeUnchanged, "unchanged published ladder needs no revalidation");
notifyReadChanges([{ scope: "intraday", date: "2026-10-27" }]);
render();
assert.equal(requests.length, beforeUnchanged, "an intraday slot cannot reload a ladder");
notifyReadChanges([{ scope: "market" }]);
render();
assert.equal(requests.length, beforeUnchanged + 1);
assert.deepEqual(render().data, knownLadder);
assert.equal(render().loading, false);
await answer(requests.at(-1), knownLadder);

path = "/market/intraday?date=2026-10-27";
render();
await answer(requests.at(-1), { slots: ["09:35"] });
const beforeSlot = requests.length;
notifyReadChanges([{ scope: "intraday", date: "2026-10-28" }]);
render();
assert.equal(requests.length, beforeSlot, "only the observed date needs another request");
notifyReadChanges([{ scope: "intraday", date: "2026-10-27" }]);
render();
assert.equal(requests.length, beforeSlot + 1);
assert.deepEqual(render().data.slots, ["09:35"]);
await answer(requests.at(-1), { slots: ["09:35", "09:45"] });
assert.deepEqual(render().data.slots, ["09:35", "09:45"]);

path = "/strategy-runs?strategy_id=s";
render();
await answer(requests.at(-1), { items: ["run-1"] });
ApiPageActivity.value = false;
render();
const beforeHiddenRun = requests.length;
notifyReadChanges([{ scope: "strategy" }]);
render();
assert.equal(requests.length, beforeHiddenRun, "hidden strategy is marked stale without fetching");
ApiPageActivity.value = true;
render();
assert.equal(requests.length, beforeHiddenRun + 1);
assert.deepEqual(render().data.items, ["run-1"]);
await answer(requests.at(-1), { items: ["run-2", "run-1"] });
assert.equal(render().data.items[0], "run-2");

ApiPageActivity.value = null;
path = "/bootstrap";
render();
await answer(requests.at(-1), { data_date: "2026-10-27" });
const beforeGlobal = requests.length;
event("workbench-route", "topics", true);
render();
assert.equal(
  requests.length,
  beforeGlobal,
  "route navigation alone does not recheck the application bootstrap",
);

const proof = (market = "published-1", dates = {}) => ({
  active_count: 0,
  read_revisions: { today: "2026-10-28", market, market_locked: false, dates, domains: { supplement: "retained-1", suspensions: null, historical_reference: "catalog-1", archive_controls: "controls-1", tasks: "tasks-1" } },
});
async function stageGainers(day, options = {}, overlappingRead = false) {
  const warmed = prefetchHistoricalReads(firstGainersReadPath("2026-10-27"), [day], "snapshot-1", new AbortController().signal, () => true);
  assert.match(requests.at(-1).url, /\/api\/v1\/tasks\/activity\?date=2026-10-\d{2}&historical=true/);
  await answer(requests.at(-1), proof());
  assert.equal(requests.at(-1).url, "/api/v1" + firstGainersReadPath(day));
  assert.equal(requests.at(-1).options.priority, "low");
  const speculative = requests.at(-1);
  const foreground = overlappingRead && api("/market/intraday/history?date=2026-10-27");
  speculative.resolve(new Response(JSON.stringify({ date: day, snapshot_id: "snapshot-1", items: [day], ...options })));
  await warmed;
  if (foreground) {
    await answer(requests.at(-1), { items: [] });
    await foreground;
  }
}
ApiPageActivity.value = true;
await stageGainers("2026-10-26", {}, true);
path = firstGainersReadPath("2026-10-26");
const beforeWarmActivation = requests.length;
render();
assert.match(requests.at(-1).url, /\/api\/v1\/tasks\/activity\?date=2026-10-\d{2}&historical=true/);
assert.equal(render().data, null, "warm data remains hidden until its revisions are rechecked");
await answer(requests.at(-1), proof());
assert.equal(requests.length, beforeWarmActivation + 1, "valid warm data eliminates the duplicate ranked-page GET");
assert.deepEqual(render().data.items, ["2026-10-26"]);

await stageGainers("2026-10-14");
path = firstGainersReadPath("2026-10-14");
const beforeRemount = requests.length;
render();
const cancelledProof = requests.at(-1);
ApiPageActivity.value = false;
render();
assert.equal(cancelledProof.options.signal.aborted, true);
ApiPageActivity.value = true;
render();
assert.match(requests.at(-1).url, /\/tasks\/activity\?date=2026-10-14&historical=true$/);
await answer(requests.at(-1), proof());
assert.equal(requests.length, beforeRemount + 2, "an aborted verification leaves the warmed response for the next reader");
assert.deepEqual(render().data.items, ["2026-10-14"]);

await stageGainers("2026-10-23");
path = firstGainersReadPath("2026-10-23");
render();
await answer(requests.at(-1), proof("published-2"));
assert.equal(render().data, null, "a superseded prefetched snapshot stays out of the page");
assert.equal(requests.at(-1).url, "/api/v1" + path);
await answer(requests.at(-1), { items: ["fresh"] });
assert.deepEqual(render().data.items, ["fresh"]);

await stageGainers("2026-10-22", { padding: "x".repeat(4 * 1024 * 1024) });
path = firstGainersReadPath("2026-10-22");
render();
assert.equal(requests.at(-1).url, "/api/v1" + path, "oversized prefetch is discarded");
await answer(requests.at(-1), { items: ["ordinary"] });

await stageGainers("2026-10-21");
now += 90001;
path = firstGainersReadPath("2026-10-21");
render();
assert.equal(requests.at(-1).url, "/api/v1" + path, "expired prefetch returns to the normal page read");
await answer(requests.at(-1), { items: ["current"] });

const auctionDay = "2026-10-20";
const auctionTarget = `/market/auction?date=${auctionDay}`;
const auctionProof = proof("published-1", { [auctionDay]: { auction: "auction-1" } });
const eligible = new AbortController();
const warmedAuction = prefetchHistoricalReads("/market/auction?date=2026-10-27", [auctionDay], "latest-auction", eligible.signal, () => true);
assert.equal(requests.at(-1).url, `/api/v1/tasks/activity?date=${auctionDay}&historical=true`);
await answer(requests.at(-1), auctionProof);
assert.equal(requests.at(-1).url, "/api/v1" + auctionTarget);
requests.at(-1).resolve(new Response(JSON.stringify({
  date: auctionDay, can_capture: false, state: "available",
  snapshot: { date: auctionDay, snapshot_id: "older-auction" },
})));
await warmedAuction;
path = auctionTarget;
const beforeAuctionSelection = requests.length;
render();
await answer(requests.at(-1), auctionProof);
assert.equal(requests.length, beforeAuctionSelection + 1);
assert.equal(render().data.snapshot.snapshot_id, "older-auction");

const missingDay = "2026-10-19";
const missing = prefetchHistoricalReads("/market/auction?date=2026-10-27", [missingDay], "latest-auction", eligible.signal, () => true);
const beforeMissingBody = requests.length;
await answer(requests.at(-1), proof("published-1", { [missingDay]: { auction: null } }));
await missing;
assert.equal(requests.length, beforeMissingBody, "an unarchived day does not start a page prefetch");

const sectorDay = "2026-10-16";
const sectorPath = `/market/sectors?date=${sectorDay}&limit=1000`;
const warmedSector = prefetchHistoricalReads("/market/sectors?date=2026-10-27&limit=1000", [sectorDay, "2026-10-15"], "snapshot-1", eligible.signal, () => true);
const beforeSectorBody = requests.length;
await prefetchHistoricalReads("/market/indices?date=2026-10-27", [sectorDay], "snapshot-1", eligible.signal, () => true);
assert.equal(requests.length, beforeSectorBody, "a second page cannot start another background chain concurrently");
await answer(requests.at(-1), proof());
assert.equal(requests.at(-1).url, "/api/v1" + sectorPath);
requests.at(-1).resolve(new Response(JSON.stringify({ date: sectorDay, snapshot_id: "snapshot-1", items: [] })));
await warmedSector;
assert.equal(requests.length, beforeSectorBody + 1, "a heavy route speculates on at most one past day");
path = sectorPath;
render();
const changedCatalog = proof();
changedCatalog.read_revisions.domains.historical_reference = "catalog-2";
await answer(requests.at(-1), changedCatalog);
assert.equal(requests.at(-1).url, "/api/v1" + sectorPath, "catalog visibility changes bypass the warmed page");
await answer(requests.at(-1), { date: sectorDay, items: ["updated"] });

const beforeUnsafePath = requests.length;
await prefetchHistoricalReads("/market/abnormal?monitor_scope=latest&limit=1000&date=2026-10-27", [sectorDay], "snapshot-1", eligible.signal, () => true);
assert.equal(requests.length, beforeUnsafePath, "latest monitoring is not an archived read");

const indexDay = "2026-10-15";
const warmedIndex = prefetchHistoricalReads("/market/indices?date=2026-10-27", [indexDay], "snapshot-1", eligible.signal, () => true);
await answer(requests.at(-1), proof());
requests.at(-1).resolve(new Response("{invalid JSON"));
await warmedIndex;
path = `/market/indices?date=${indexDay}`;
render();
await answer(requests.at(-1), proof());
assert.equal(requests.at(-1).url, "/api/v1" + path, "a malformed raw candidate falls back only when consumed");
await answer(requests.at(-1), { date: indexDay, indices: [] });
assert.ok(
  requests.every((request) => !request.options.method),
  "visibility only rereads GETs",
);
effects.forEach((effect) => effect?.cleanup?.());
delete globalThis.__pageHooks;
delete globalThis.window;
delete globalThis.fetch;
globalThis.performance = savedPerformance;
console.log(
  "retained-page reads: cancel, fresh activation, date isolation, edit and unlock invalidation passed",
);
