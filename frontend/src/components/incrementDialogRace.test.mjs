// Run the actual dialog's effects against controlled deferred HTTP responses.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const slots = [],
  effects = [],
  listeners = new Map(),
  requests = [],
  restored = [];
let cursor = 0,
  pending = [],
  current = { requestKey: "request-a", createdAt: 100 },
  finished = 0;
const same = (a, b) =>
  a?.length === b?.length && a.every((x, i) => Object.is(x, b[i]));
globalThis.__incrementHooks = {
  useState(initial) {
    const i = cursor++;
    if (!(i in slots))
      slots[i] = typeof initial === "function" ? initial() : initial;
    return [
      slots[i],
      (value) => {
        slots[i] = typeof value === "function" ? value(slots[i]) : value;
      },
    ];
  },
  useRef(initial) {
    const i = cursor++;
    return (slots[i] ||= { current: initial });
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
const root = { inert: false };
globalThis.document = {
  hidden: false,
  body: { children: [], style: { overflow: "" } },
  getElementById: () => root,
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = {
  addEventListener(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
  },
  removeEventListener(type, fn) {
    listeners.get(type)?.delete(fn);
  },
};
function event(value) {
  current = value;
  for (const fn of listeners.get("session") || []) fn({ detail: value });
}
globalThis.MutationObserver = class {
  observe() {}
  disconnect() {}
};
globalThis.__incrementAPI = {
  api: (path) =>
    new Promise((resolve, reject) => requests.push({ path, resolve, reject })),
  post: async () => ({}),
};
globalThis.__incrementSession = {
  readIncrementSession: () => current,
  restoreIncrementSession(value) {
    restored.push(value);
    event({
      requestKey: value.request_key,
      taskId: value.root_task_id,
      createdAt: 100,
      epoch: value.session_epoch,
    });
  },
  acknowledgeIncrementSession() {
    event(null);
  },
};
const timers = new Map();
let timerId = 0;
const realTimers = [
  globalThis.setTimeout,
  globalThis.clearTimeout,
  globalThis.setInterval,
  globalThis.clearInterval,
];
globalThis.setTimeout = globalThis.setInterval = (fn) => {
  timers.set(++timerId, fn);
  return timerId;
};
globalThis.clearTimeout = globalThis.clearInterval = (id) => timers.delete(id);
const progress = ts.transpileModule(
  await fs.readFile(new URL("./incrementProgress.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.ES2022 } },
).outputText;
const imports = {
  react: encode(
    "export const {useState,useRef,useEffect}=globalThis.__incrementHooks;",
  ),
  "react-dom": encode("export const createPortal=()=>null;"),
  "../lib/api": encode("export const {api,post}=globalThis.__incrementAPI;"),
  "../lib/format": encode(
    "export const dateTime=String,duration=String,statusText={};",
  ),
  "./incrementSession": encode(
    'export const INCREMENT_SESSION_EVENT="session",INCREMENT_SESSION_KEY="key";export const {readIncrementSession,restoreIncrementSession,acknowledgeIncrementSession}=globalThis.__incrementSession;',
  ),
  "./incrementProgress": encode(progress),
};
let source = ts
  .transpileModule(
    await fs.readFile(
      new URL("./IncrementProgressDialog.tsx", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  )
  .outputText.replace(/import\s+["'][^"']+\.css["'];?/g, "");
for (const m of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    m[0],
    "from " + JSON.stringify(imports[m[2]] || import.meta.resolve(m[2])),
  );
const { default: Dialog } = await import(encode(source));
const properties = {
  onFinished() {
    finished++;
  },
  onBlockedChange() {},
};
function render() {
  cursor = 0;
  pending = [];
  Dialog(properties);
  for (const fn of pending) fn();
}
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};
try {
  render();
  assert.equal(root.inert, true);
  const discover = requests.find((x) => x.path === "/sync/session");
  const progressRequest = requests.find((x) => x.path.includes("request_key="));
  const receipt = {
    request_key: "request-a",
    root_task_id: "job-a",
    session_epoch: "epoch-a",
    created_at_ms: 100,
    locked: true,
    can_acknowledge: false,
    status: "running",
    tasks: [],
    elapsed_ms: 1234,
  };
  progressRequest.resolve(receipt);
  await flush();
  render();
  assert.equal(
    slots[1],
    receipt,
    "accepting the task ID and epoch must not erase the receipt from this same request",
  );
  assert.equal(root.inert, true);
  const lateProgress = requests.find((item) => item.path.includes("task_id="));
  assert.ok(lateProgress);
  event(null);
  // Resolve before React commits the cleanup, the narrow acknowledgement race.
  lateProgress.resolve(receipt);
  await flush();
  render();
  assert.equal(root.inert, false);
  assert.equal(finished, 1);
  discover.resolve({ active: receipt });
  await flush();
  render();
  assert.equal(
    restored.length,
    1,
    "a discovery GET started before acknowledgement must not restore the dismissed session",
  );
  assert.equal(current, null);
  assert.equal(root.inert, false);
  assert.equal(finished, 1);
} finally {
  effects.forEach((effect) => effect?.cleanup?.());
  [
    globalThis.setTimeout,
    globalThis.clearTimeout,
    globalThis.setInterval,
    globalThis.clearInterval,
  ] = realTimers;
}
console.log(
  "Increment effects: receipt continuity, inert lifecycle and stale-discovery/ack race passed",
);
