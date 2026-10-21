/** Deterministic scheduler and mocked read API; no runtime jobs/network are created. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
async function compiledAt(path) {
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
const timers = new Map(),
  requests = [],
  pending = [],
  events = [],
  documentListeners = new Map();
let nextTimer = 0;
globalThis.setTimeout = (fn, delay) => {
  const id = ++nextTimer;
  timers.set(id, { fn, delay });
  return id;
};
globalThis.clearTimeout = (id) => timers.delete(id);
globalThis.document = {
  hidden: false,
  addEventListener: (event, fn) => documentListeners.set(event, fn),
  removeEventListener: (event) => documentListeners.delete(event),
};
globalThis.window = { dispatchEvent: (event) => events.push(event) };
globalThis.CustomEvent = class {
  constructor(type, options) {
    this.type = type;
    this.detail = options.detail;
  }
};
globalThis.__activityRequests = requests;
globalThis.__activityPending = pending;
const react = encode(
  "export function useSyncExternalStore(subscribe,get){globalThis.__subscribeActivity=subscribe;return get();}",
);
const api = encode(
  "export function api(path, options){globalThis.__activityRequests.push({path,options});return new Promise(resolve=>globalThis.__activityPending.push(resolve));}",
);
const presentation = encode(await compiledAt("./taskActivityPresentation.ts"));
const workbenchEvents = encode(await compiledAt("../lib/workbenchEvents.ts"));
let source = await compiledAt("./taskActivity.ts");
source = source
  .replaceAll('"react"', JSON.stringify(react))
  .replaceAll('"../lib/api"', JSON.stringify(api))
  .replaceAll('"./taskActivityPresentation"', JSON.stringify(presentation))
  .replaceAll('"../lib/workbenchEvents"', JSON.stringify(workbenchEvents));
const activity = await import(encode(source));
activity.useTaskActivity();
const stop = globalThis.__subscribeActivity(() => {});
const tick = async () => {
  const [id, timer] = timers.entries().next().value;
  timers.delete(id);
  timer.fn();
  await Promise.resolve();
  return timer.delay;
};
const resolve = async (items, active_count, observed_at_ms = Date.now()) => {
  pending.shift()({ items, active_count, observed_at_ms });
  await Promise.resolve();
  await Promise.resolve();
};
assert.equal(await tick(), 0);
assert.equal(requests.length, 1);
assert.equal(requests[0].path, "/tasks/activity");
await resolve([], 0, 123456);
assert.equal(
  activity.useTaskActivity().clock.observedAtMs,
  123456,
  "notice time anchors to the actual server observation, not the client's absolute clock",
);
assert.equal(
  Number.isFinite(activity.useTaskActivity().clock.receivedMonotonicMs),
  true,
);
assert.equal(
  timers.values().next().value.delay,
  30000,
  "idle uses low-frequency polling",
);
activity.startTaskRequest({
  request_key: "request",
  kind: "selection",
  strategy_id: "s",
  title: "个股策略选股",
});
assert.equal(activity.useTaskActivity().pending[0].status, "submitting");
await tick();
await resolve(
  [
    {
      job_id: "job",
      request_key: "request",
      kind: "selection",
      strategy_id: "s",
      status: "running",
      progress: 0,
    },
  ],
  1,
);
assert.equal(activity.strategyTaskBusy(activity.useTaskActivity(), "s"), true);
assert.equal(activity.useTaskActivity().tasks[0].progress, 0);
assert.equal(timers.values().next().value.delay, 2000);
document.hidden = true;
documentListeners.get("visibilitychange")();
assert.equal(timers.size, 0, "hidden pages stop scheduled polling");
assert.equal(requests.length, 2);
document.hidden = false;
documentListeners.get("visibilitychange")();
assert.equal(
  await tick(),
  0,
  "visibility restoration reads current real state",
);
await resolve(
  [
    {
      job_id: "job",
      request_key: "request",
      kind: "selection",
      strategy_id: "s",
      status: "succeeded",
      finished_at_ms: Date.now(),
      progress: 100,
    },
  ],
  0,
);
activity.finishTaskRequest("request");
assert.equal(activity.useTaskActivity().pending.length, 0);
assert.equal(activity.strategyTaskBusy(activity.useTaskActivity(), "s"), false);
assert.equal(
  events.filter((event) => event.type === "workbench-route").length,
  1,
  "completion restores server results after a lost POST response",
);
assert.equal(
  requests.every(
    (request) => request.path === "/tasks/activity" && !request.options.method,
  ),
  true,
  "poller only performs read requests",
);
stop();
assert.equal(timers.size, 0);
assert.equal(documentListeners.size, 0);
console.log(
  "Task activity store: shared read polling, pending guard, actual progress, hidden pause, completion refresh and cleanup passed",
);
