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
const readChanges = [];
globalThis.__readChanges = readChanges;
const react = encode(
  "export function useSyncExternalStore(subscribe,get){globalThis.__subscribeActivity=subscribe;return get();}",
);
const api = encode(
  "export function api(path, options){globalThis.__activityRequests.push({path,options});return new Promise((resolve,reject)=>{const accept=value=>resolve(value);accept.reject=reject;globalThis.__activityPending.push(accept);});} export function notifyReadChanges(changes){globalThis.__readChanges.push(...changes)} export function observeReadScopes(){}",
);
const presentation = encode(await compiledAt("./taskActivityPresentation.ts"));
const workbenchEvents = encode(await compiledAt("../lib/workbenchEvents.ts"));
const { MARKET_INCREMENT_FINISHED } = await import(workbenchEvents);
assert.equal(MARKET_INCREMENT_FINISHED, "workbench-market-increment-finished");
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
const resolve = async (items, active_count, observed_at_ms = Date.now(), read_revisions) => {
  pending.shift()({ items, active_count, observed_at_ms, read_revisions });
  await Promise.resolve();
  await Promise.resolve();
};
assert.equal(await tick(), 0);
assert.equal(requests.length, 1);
assert.equal(requests[0].path, "/tasks/activity");
const oldIncrement = { job_id: "old-increment", kind: "increment", status: "succeeded", target_date: null, finished_at_ms: 123000 };
await resolve([oldIncrement], 0, 123456);
assert.equal(events.filter(event => event.type === MARKET_INCREMENT_FINISHED).length, 0,
  "the first successful poll records old completed increments without announcing a new publish");
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

const revision = (strategy, intraday, extra = {}) => ({
  today: "2026-11-10",
  market: "old-market",
  domains: { strategy, tasks: "tasks" },
  dates: { "2026-11-10": { intraday } },
  ...extra,
});
const probe = async (revisions) => {
  activity.refreshTaskActivity();
  await tick();
  await resolve([], 0, 1000000, revisions);
};
await probe(revision(null, null));
assert.deepEqual(readChanges, [], "first revision observation is a baseline");
await probe(revision("run-2", "09:45", { market_locked: true, market: "staging" }));
assert.deepEqual(readChanges, [], "locked publication is ignored");
await probe(revision("run-2", "09:45", { market: "published" }));
assert.deepEqual(readChanges, [
  { scope: "strategy" },
  { scope: "market" },
  { scope: "intraday", date: "2026-11-10" },
], "durable revisions refresh saved results even after recent receipts aged out");
await probe(revision("run-2", "09:45", { market: "published" }));
assert.equal(readChanges.length, 3, "identical revision does not trigger a second read");

const incrementEvents = () => events.filter(event => event.type === MARKET_INCREMENT_FINISHED);
const increment = (job_id, status = "succeeded", finished_at_ms = 900000, target_date = null) => ({
  job_id, kind: "increment", status, finished_at_ms, target_date,
});
const pollItems = async (store, items, observed = 1000000) => {
  store.refreshTaskActivity();
  await tick();
  await resolve(items, items.filter(task => ["running", "queued"].includes(task.status)).length, observed);
};
await pollItems(activity, [oldIncrement]);
assert.equal(incrementEvents().length, 0, "baseline completion remains deduplicated after other receipts age out");
const stoppedStates = ["running", "queued", "paused", "failed", "cancelled"];
await pollItems(activity, stoppedStates.map((status, index) => increment("resumed-" + index, status, null)));
const completed = stoppedStates.map((_, index) => ({
  ...increment("resumed-" + index, ["succeeded", "partial", "no_change"][index % 3], 900001 + index,
    index === 1 ? "2026-10-29" : null),
  parameters: { not_public: "synthetic-private-metadata" },
}));
await pollItems(activity, completed);
assert.equal(incrementEvents().length, 1, "one poll batches all explicitly completed market increments");
assert.deepEqual(incrementEvents()[0].detail, { tasks: completed.map(task => ({
  job_id: task.job_id, target_date: task.target_date, finished_at_ms: task.finished_at_ms, status: task.status,
})) }, "event projects only opaque identity, nullable date and successful completion metadata");
await pollItems(activity, completed);
assert.equal(incrementEvents().length, 1, "repeated identical receipts cannot re-announce a publish");
await pollItems(activity, [increment("resumed-0", "running", 900001)]);
await pollItems(activity, [increment("resumed-0", "partial", 900001)]);
assert.equal(incrementEvents().length, 1, "status re-entry with the same completion identity stays deduplicated");
await pollItems(activity, [increment("resumed-0", "succeeded", 900010)]);
assert.equal(incrementEvents().length, 2, "a new completion time identifies a new finished attempt");
await pollItems(activity, [increment("short-task", "no_change", 900020)]);
assert.equal(incrementEvents().length, 3, "a short increment need not have appeared as running first");
const beforeUnrelated = incrementEvents().length;
await pollItems(activity, [
  ...["failed", "cancelled", "paused", "queued", "running"].map((status, index) => increment("not-success-" + index, status)),
  { ...increment("initialisation"), kind: "initialise" },
  { ...increment("noon"), kind: "source_check", operation: "noon_snapshot" },
  { ...increment("auction"), kind: "source_check", operation: "auction_snapshot" },
  { ...increment("other-source"), kind: "source_check", operation: "market_supplement" },
  { ...increment("not-an-increment"), kind: "selection" },
]);
await pollItems(activity, []);
assert.equal(incrementEvents().length, beforeUnrelated,
  "neither unrelated tasks nor the disappearance of active work proves a successful increment");
await pollItems(activity, [
  ...[null, "", " ", 7, " padded ", "x".repeat(201)].map(job_id => increment(job_id)),
  ...[null, "900000", NaN, Infinity, -Infinity, 0, -1, 1000001].map((finished, index) => increment("bad-time-" + index, "succeeded", finished)),
]);
assert.equal(incrementEvents().length, beforeUnrelated, "invalid IDs and nonfinite, missing, nonpositive or future timestamps do not emit");
await pollItems(activity, [increment("bad-time-7", "succeeded", 1000001)], 1000002);
assert.equal(incrementEvents().length, beforeUnrelated + 1, "a previously future receipt is not consumed before the server clock permits it");
await pollItems(activity, [increment("missing-server-clock")], NaN);
assert.equal(incrementEvents().length, beforeUnrelated + 1, "client-clock fallback cannot establish successful increment timing");
await pollItems(activity, [increment("missing-server-clock")]);
assert.equal(incrementEvents().length, beforeUnrelated + 2, "a later valid server observation can announce the previously unverified receipt");
await pollItems(activity, [increment("duplicate-terminal"), increment("duplicate-terminal")]);
assert.equal(incrementEvents().at(-1).detail.tasks.length, 1, "duplicate rows in one response emit one completion identity");
const recent = Array.from({ length: 256 }, (_, index) => increment("bounded-" + index, "succeeded", 910000 + index));
await pollItems(activity, recent);
const beforeBoundedReplay = incrementEvents().length;
await pollItems(activity, recent);
await pollItems(activity, []);
await pollItems(activity, [recent[0], recent.at(-1)]);
assert.equal(incrementEvents().length, beforeBoundedReplay, "the bounded recent-completion window retains all 256 receipt identities");
stop();
assert.equal(timers.size, 0);
assert.equal(documentListeners.size, 0);

// Fresh store instances prove failures/aborts cannot prematurely establish the
// initial baseline; mocked requests deliberately still resolve after abortion.
const freshStore = async suffix => {
  const store = await import(encode(source + "\n// isolated baseline scenario " + suffix));
  store.useTaskActivity();
  return { store, unsubscribe: globalThis.__subscribeActivity(() => {}) };
};
let isolated = await freshStore("failed-first-request");
await tick();
pending.shift().reject(new Error("synthetic first poll failure"));
await Promise.resolve(); await Promise.resolve();
const beforeInitialRecovery = incrementEvents().length;
await pollItems(isolated.store, [increment("old-after-error")]);
assert.equal(incrementEvents().length, beforeInitialRecovery, "the first accepted response after an error remains baseline-only");
await pollItems(isolated.store, [increment("new-after-error")]);
assert.equal(incrementEvents().length, beforeInitialRecovery + 1);
isolated.unsubscribe();
isolated = await freshStore("aborted-first-request");
await tick();
const aborted = pending.shift();
document.hidden = true;
documentListeners.get("visibilitychange")();
aborted({ items: [increment("aborted-terminal")], active_count: 0, observed_at_ms: 1000000 });
await Promise.resolve(); await Promise.resolve();
assert.equal(timers.size, 0);
const beforeAbortRecovery = incrementEvents().length;
document.hidden = false;
documentListeners.get("visibilitychange")();
await tick(); await resolve([increment("old-after-abort")], 0, 1000000);
assert.equal(incrementEvents().length, beforeAbortRecovery, "hidden aborted response cannot consume the baseline");
await pollItems(isolated.store, [increment("aborted-terminal")]);
assert.equal(incrementEvents().length, beforeAbortRecovery + 1, "aborted response cannot consume an unseen completion key");
isolated.unsubscribe();
isolated = await freshStore("invalid-first-server-clock");
const beforeInvalidClock = incrementEvents().length;
await pollItems(isolated.store, [increment("unverified-old")], NaN);
await pollItems(isolated.store, [increment("unverified-old")]);
assert.equal(incrementEvents().length, beforeInvalidClock, "invalid server observation cannot advance the first accepted baseline");
await pollItems(isolated.store, [increment("verified-new")]);
assert.equal(incrementEvents().length, beforeInvalidClock + 1);
isolated.unsubscribe();
assert.equal(timers.size, 0);
assert.equal(documentListeners.size, 0);
console.log(
  "Task activity store: shared read polling, pending guard, hidden pause, market increment baseline/dedupe/strict receipts, abort recovery and cleanup passed",
);
