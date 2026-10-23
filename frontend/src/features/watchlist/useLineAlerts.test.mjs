import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encoded = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = async (path) =>
  ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
const helpers = encoded(await compile("./lineAlertPresentation.ts"));
const slots = [];
let cursor = 0,
  dirty = false,
  pendingEffects = [];
const same = (a, b) =>
  a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
globalThis.__lineHooks = {
  useState(initial) {
    const i = cursor++;
    if (!slots[i])
      slots[i] = { value: typeof initial === "function" ? initial() : initial };
    return [
      slots[i].value,
      (next) => {
        const value = typeof next === "function" ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) {
          slots[i].value = value;
          dirty = true;
        }
      },
    ];
  },
  useRef(value) {
    const i = cursor++;
    if (!slots[i]) slots[i] = { value: { current: value } };
    return slots[i].value;
  },
  useCallback(callback, deps) {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps))
      slots[i] = { deps, value: callback };
    return slots[i].value;
  },
  useEffect(callback, deps) {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps)) {
      const previous = slots[i];
      slots[i] = { deps, cleanup: previous?.cleanup };
      pendingEffects.push(() => {
        slots[i].cleanup?.();
        slots[i].cleanup = callback();
      });
    }
  },
};
const windowTarget = new EventTarget(),
  documentTarget = new EventTarget();
globalThis.window = windowTarget;
globalThis.document = Object.assign(documentTarget, { hidden: false });
globalThis.location = { hash: "#watchlist" };
globalThis.__lineActivity = { tasks: [], pending: [] };
globalThis.__linePaths = [];
globalThis.__linePosts = [];
globalThis.__lineJobs = {};
let reloads = 0,
  memberships = 0,
  keys = 0,
  fail = false;
const reload = () => {
  reloads++;
};
globalThis.__lineApi = {
  useApi(path) {
    globalThis.__linePaths.push(path);
    return {
      data: path?.startsWith("/tasks/")
        ? globalThis.__lineJobs[path.slice(7)] || null
        : null,
      loading: false,
      error: "",
      reload,
    };
  },
  requestKey: () => `line-request-${++keys}`,
  async post(path, body) {
    globalThis.__linePosts.push({ path, body });
    await Promise.resolve();
    if (fail) throw Error("uncertain network response");
    return {
      job_id: "line-job",
      status: "queued",
      parameters: { operation: "line_alert_check" },
    };
  },
};
globalThis.__lineTaskStore = {
  useTaskActivity: () => globalThis.__lineActivity,
  startTaskRequest: () => {},
  finishTaskRequest: () => {},
};
globalThis.__lineMembership = {
  WATCHLIST_CHANGED: "watchlist-test-changed",
  refreshWatchlistMembership: () => {
    memberships++;
    return Promise.resolve();
  },
};
let source = await compile("./useLineAlerts.ts");
for (const [path, replacement] of Object.entries({
  react: encoded(
    "export const {useCallback,useEffect,useRef,useState}=globalThis.__lineHooks;",
  ),
  "../../lib/api": encoded(
    "export const {post,requestKey,useApi}=globalThis.__lineApi;",
  ),
  "../../components/taskActivity": encoded(
    "export const {finishTaskRequest,startTaskRequest,useTaskActivity}=globalThis.__lineTaskStore;",
  ),
  "./lineAlertPresentation": helpers,
  "./watchlistStore": encoded(
    "export const {refreshWatchlistMembership,WATCHLIST_CHANGED}=globalThis.__lineMembership;",
  ),
}))
  source = source.replaceAll(
    `from ${JSON.stringify(path)}`,
    `from ${JSON.stringify(replacement)}`,
  );
const { useLineAlerts } = await import(encoded(source));
let value;
function render() {
  for (let pass = 0; pass < 20; pass++) {
    cursor = 0;
    dirty = false;
    pendingEffects = [];
    value = useLineAlerts("2026-10-21");
    for (const effect of pendingEffects) effect();
    if (!dirty) return value;
  }
  throw Error("hook did not settle");
}
render();
assert.equal(
  globalThis.__linePosts.length,
  0,
  "initial load performs no evaluation",
);
assert.ok(
  globalThis.__linePaths.includes(
    "/watchlist/line-alerts?limit=100&as_of=2026-10-21",
  ),
);
globalThis.location.hash = "#market";
windowTarget.dispatchEvent(new Event("hashchange"));
render();
assert.deepEqual(
  globalThis.__linePaths.slice(-2),
  [null, null],
  "retained hidden page disables alert and job reads",
);
globalThis.__lineActivity = {
  tasks: [
    {
      job_id: "auto-check",
      kind: "source_check",
      operation: "line_alert_check",
      status: "succeeded",
      finished_at_ms: 100,
    },
  ],
  pending: [],
};
const beforeHidden = reloads;
render();
assert.equal(reloads, beforeHidden, "hidden completion waits for visible page");
globalThis.location.hash = "#watchlist";
windowTarget.dispatchEvent(new Event("hashchange"));
render();
assert.ok(reloads > beforeHidden);
assert.equal(memberships, 1);
render();
assert.equal(
  memberships,
  1,
  "same persisted completion does not cause a reload loop",
);
fail = true;
await value.evaluate();
render();
assert.match(value.error, /uncertain/);
const retryKey = globalThis.__linePosts.at(-1).body.request_key;
fail = false;
await Promise.all([value.evaluate(), value.evaluate()]);
render();
assert.equal(
  globalThis.__linePosts.length,
  2,
  "double click and uncertain retry submit only once each",
);
assert.equal(globalThis.__linePosts.at(-1).body.request_key, retryKey);
assert.equal(value.busy, true);
globalThis.__lineActivity = {
  tasks: [
    {
      job_id: "line-job",
      request_key: retryKey,
      kind: "source_check",
      operation: "line_alert_check",
      status: "succeeded",
      finished_at_ms: 200,
    },
  ],
  pending: [],
};
render();
assert.equal(value.busy, false);
await value.evaluate();
render();
assert.notEqual(
  globalThis.__linePosts.at(-1).body.request_key,
  retryKey,
  "confirmed completed request does not block an intentional new check",
);
globalThis.__lineActivity = { tasks: [], pending: [] };
globalThis.__lineJobs["line-job"] = {
  job_id: "line-job",
  kind: "source_check",
  parameters: { operation: "line_alert_check" },
  status: "succeeded",
  finished_at_ms: 300,
};
render();
assert.equal(
  value.busy,
  false,
  "manual job aged out of the compact activity feed recovers its exact durable status",
);
assert.ok(globalThis.__linePaths.includes("/tasks/line-job"));
documentTarget.hidden = true;
documentTarget.dispatchEvent(new Event("visibilitychange"));
render();
assert.deepEqual(globalThis.__linePaths.slice(-2), [null, null]);
const posts = globalThis.__linePosts.length;
for (const slot of slots) slot?.cleanup?.();
assert.equal(
  globalThis.__linePosts.length,
  posts,
  "unmount never creates work",
);
console.log(
  "Line alert controller: GET-only mount, hidden-page stop, durable completion refresh, exact-job recovery and idempotent manual retry passed",
);
