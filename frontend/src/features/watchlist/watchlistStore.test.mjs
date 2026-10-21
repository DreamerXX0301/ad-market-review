/** Actual shared membership and activity stores; mocked GET transport, no live jobs. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
async function compiled(path) {
  return ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
}
class Surface extends EventTarget {
  listeners = new Map();
  addEventListener(type, fn) {
    super.addEventListener(type, fn);
    const set = this.listeners.get(type) || new Set();
    set.add(fn);
    this.listeners.set(type, set);
  }
  removeEventListener(type, fn) {
    super.removeEventListener(type, fn);
    this.listeners.get(type)?.delete(fn);
  }
}
const windowSurface = new Surface(),
  documentSurface = Object.assign(new Surface(), { hidden: false });
globalThis.window = windowSurface;
globalThis.document = documentSurface;
const requests = [],
  waiting = [],
  timers = new Map(),
  subscribers = [];
let timerId = 0,
  changes = 0;
globalThis.setTimeout = (fn, delay) => {
  const id = ++timerId;
  timers.set(id, { fn, delay });
  return id;
};
globalThis.clearTimeout = (id) => timers.delete(id);
globalThis.__memberSubscriptions = subscribers;
globalThis.__memberGet = (path, options = {}) => {
  assert.equal(options.method, undefined, "integration cannot POST or mutate");
  requests.push(path);
  return new Promise((resolve) => waiting.push({ path, resolve }));
};
const react = encode(
  "export const useEffect=(fn)=>fn();export const useSyncExternalStore=(subscribe,get)=>{globalThis.__memberSubscriptions.push(subscribe);return get();};",
);
const api = encode(
  "export const api=globalThis.__memberGet;export const post=()=>{throw Error('no mutations in store integration')};",
);
const events = encode(await compiled("../../lib/workbenchEvents.ts"));
const presentation = encode(
  await compiled("../../components/taskActivityPresentation.ts"),
);
let memberSource = await compiled("./watchlistStore.ts");
memberSource = memberSource
  .replaceAll('"react"', JSON.stringify(react))
  .replaceAll('"../../lib/api"', JSON.stringify(api))
  .replaceAll('"../../lib/workbenchEvents"', JSON.stringify(events));
let activitySource = await compiled("../../components/taskActivity.ts");
activitySource = activitySource
  .replaceAll('"react"', JSON.stringify(react))
  .replaceAll('"../lib/api"', JSON.stringify(api))
  .replaceAll('"../lib/workbenchEvents"', JSON.stringify(events))
  .replaceAll('"./taskActivityPresentation"', JSON.stringify(presentation));
const membership = await import(encode(memberSource));
const activity = await import(encode(activitySource));
const { LINE_ALERT_CHECK_FINISHED } = await import(events);
windowSurface.addEventListener(membership.WATCHLIST_CHANGED, () => changes++);
membership.useWatchlistMembership();
const stopMembership = subscribers.shift()(() => {});
activity.useTaskActivity();
const stopActivity = subscribers.shift()(() => {});
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
const tick = async () => {
  assert.ok(timers.size, "a shared activity poll is scheduled");
  const [id, timer] = timers.entries().next().value;
  timers.delete(id);
  timer.fn();
  await flush();
  return timer.delay;
};
const answer = async (path, data) => {
  const index = waiting.findIndex((item) => item.path === path);
  assert.ok(index >= 0, `pending ${path}`);
  waiting.splice(index, 1)[0].resolve(data);
  await flush();
};
const receipt = (id, finished = 100) => ({
  job_id: id,
  kind: "source_check",
  operation: "line_alert_check",
  status: "succeeded",
  finished_at_ms: finished,
});
const poll = async (items) => {
  await tick();
  await answer("/tasks/activity", {
    items,
    active_count: items.filter((t) => ["queued", "running"].includes(t.status))
      .length,
    observed_at_ms: 10000,
  });
};
assert.equal(requests.filter((path) => path === "/watchlist/ids").length, 1);
await poll([receipt("auto-first")]);
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  1,
  "completion waits for an earlier pre-completion ID response",
);
await answer("/watchlist/ids", { instrument_ids: [] });
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  2,
  "then reads the committed membership instead of reusing the stale in-flight response",
);
await answer("/watchlist/ids", { instrument_ids: ["605089.SH"] });
assert.equal(
  membership.useWatchlistMembership().ids.has("605089.SH"),
  true,
  "a stock detail star becomes selected without ever mounting the watchlist page",
);
assert.equal(changes, 1);
const reads = requests.length;
await poll([receipt("auto-first")]);
await poll([]);
await poll([receipt("auto-first")]);
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  2,
  "same expired success, feed removal and reappearance never refresh on every poll",
);
assert.equal(requests.length, reads + 3);
await poll([
  {
    job_id: "long-hidden",
    kind: "source_check",
    operation: "line_alert_check",
    status: "running",
  },
]);
documentSurface.hidden = true;
documentSurface.dispatchEvent(new Event("visibilitychange"));
assert.equal(
  timers.size,
  0,
  "browser-hidden activity performs no ongoing polling",
);
documentSurface.hidden = false;
documentSurface.dispatchEvent(new Event("visibilitychange"));
await poll([]);
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  3,
  "an active check which aged out of the five terminal receipts still refreshes saved IDs once",
);
await answer("/watchlist/ids", { instrument_ids: ["605089.SH", "002436.SZ"] });
assert.equal(membership.useWatchlistMembership().ids.has("002436.SZ"), true);
await poll([]);
assert.equal(requests.filter((path) => path === "/watchlist/ids").length, 3);
await poll([receipt("next", 200)]);
await poll([receipt("third", 300), receipt("next", 200)]);
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  4,
  "overlapping completions serialize ID reads",
);
await answer("/watchlist/ids", { instrument_ids: ["605089.SH", "002436.SZ"] });
assert.equal(
  requests.filter((path) => path === "/watchlist/ids").length,
  5,
  "a completion arriving during a read is captured by one follow-up",
);
await answer("/watchlist/ids", {
  instrument_ids: ["605089.SH", "002436.SZ", "600000.SH"],
});
assert.equal(membership.useWatchlistMembership().ids.has("600000.SH"), true);
assert.ok(
  requests.every((path) =>
    ["/watchlist/ids", "/tasks/activity"].includes(path),
  ),
  "only bounded IDs and the existing compact task feed are read",
);
stopMembership();
stopActivity();
assert.equal(windowSurface.listeners.get(LINE_ALERT_CHECK_FINISHED)?.size, 0);
assert.equal(documentSurface.listeners.get("visibilitychange")?.size, 0);
assert.equal(timers.size, 0);
console.log(
  "Watchlist/activity integration: cross-page auto-add updates star, in-flight freshness, expired receipt dedupe, hidden stop, aged-out recovery and serial completion bursts passed",
);
