import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const state = [],
  refs = [],
  callbacks = [],
  effects = [],
  requests = [],
  listeners = new Map();
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
const { useApi, ApiPageActivity } = await import(encode(source));
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

ApiPageActivity.value = null;
path = "/bootstrap";
render();
await answer(requests.at(-1), { data_date: "2026-10-27" });
const beforeGlobal = requests.length;
event("workbench-route", "topics", true);
render();
assert.equal(
  requests.length,
  beforeGlobal + 1,
  "application-level readers retain their existing explicit refresh semantics",
);
assert.ok(
  requests.every((request) => !request.options.method),
  "visibility only rereads GETs",
);
effects.forEach((effect) => effect?.cleanup?.());
delete globalThis.__pageHooks;
delete globalThis.window;
delete globalThis.fetch;
console.log(
  "retained-page reads: cancel, fresh activation, date isolation, edit and unlock invalidation passed",
);
