/** A real banner timer must remove terminal notices before the next idle API poll. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const states = [], effects = [], queuedEffects = [], timers = new Map(), documentListeners = new Map();
let cursor = 0, timerId = 0, elapsed = 0;
const wallStart = 1_000_000_000, serverStart = 1_000_000;
Date.now = () => wallStart + elapsed;
Object.defineProperty(globalThis, "performance", { configurable: true, value: { now: () => elapsed } });
globalThis.window = { setTimeout: (fn, delay) => { timers.set(++timerId, { fn, due: elapsed + delay }); return timerId; }, clearTimeout: id => timers.delete(id) };
globalThis.document = { hidden: false, addEventListener: (event, fn) => documentListeners.set(event, fn), removeEventListener: event => documentListeners.delete(event) };
const storage = new Map();
globalThis.sessionStorage = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) };
const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
globalThis.__bannerHooks = {
  useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
  useEffect(effect, deps) { const index = cursor++; if (!effects[index] || !same(effects[index].deps, deps)) queuedEffects.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }); },
};
const react = encode("export const {useState,useEffect}=globalThis.__bannerHooks;");
const jsx = encode("export const jsx=(type,props)=>({type,props});export const jsxs=jsx;");
const icons = encode("export const CheckCircle2='check',CircleAlert='alert',ExternalLink='external',LoaderCircle='loader',X='close';");
const taskStore = encode("export const useTaskActivity=()=>globalThis.__bannerFixture;");
async function compile(path, jsx = false) { return ts.transpileModule(await fs.readFile(new URL(path, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) } }).outputText; }
const presentation = encode(await compile("./taskActivityPresentation.ts"));
let source = await compile("./ActiveTaskBanner.tsx", true);
source = source.replaceAll('"react/jsx-runtime"', JSON.stringify(jsx)).replaceAll('"react"', JSON.stringify(react)).replaceAll('"lucide-react"', JSON.stringify(icons)).replaceAll('"./taskActivityPresentation"', JSON.stringify(presentation)).replaceAll('"./taskActivity"', JSON.stringify(taskStore)).replace('import "./ActiveTaskBanner.css";', "");
const { default: Banner } = await import(encode(source));
const done = (id, status = "succeeded", finished = serverStart) => ({ job_id: id, status, finished_at_ms: finished, title: id });
function fixture(tasks) { globalThis.__bannerFixture = { tasks, pending: [], error: "", clock: { observedAtMs: serverStart + elapsed, receivedAtMs: Date.now(), receivedMonotonicMs: performance.now() } }; }
function render() { cursor = 0; const tree = Banner({ onNavigate() {} }); queuedEffects.splice(0).forEach(effect => effect()); return tree; }
function rowCount(tree) { return tree ? tree.props.children[0].length : 0; }
function advance(ms) {
  const target = elapsed + ms;
  while (true) {
    const next = [...timers.entries()].sort((a, b) => a[1].due - b[1].due)[0];
    if (!next || next[1].due > target) break;
    timers.delete(next[0]); elapsed = next[1].due; next[1].fn(); render();
  }
  elapsed = target; return render();
}
fixture([done("fresh")]);
assert.equal(rowCount(render()), 1);
assert.equal([...timers.values()][0].due, 6000);
advance(2000);
fixture([done("fresh")]); render();
assert.equal([...timers.values()][0].due, 6000, "a repeated API receipt cannot renew the six-second deadline");
assert.equal(rowCount(advance(3999)), 1);
assert.equal(advance(1), null, "completion disappears without any API poll");
fixture([done("fresh")]); assert.equal(render(), null, "polling an expired task cannot revive it");
const finish = serverStart + elapsed;
fixture([done("success", "no_change", finish), done("failure", "failed", finish)]);
assert.equal(rowCount(render()), 2);
assert.equal(rowCount(advance(6000)), 1, "each terminal has an independent lifetime");
assert.equal(advance(4000), null);
fixture([{ job_id: "active", status: "running", created_at_ms: 1 }, done("partial", "partial", serverStart + elapsed)]);
assert.equal(rowCount(render()), 2);
assert.equal(rowCount(advance(6000)), 1, "active task remains after all terminal notices expire");
assert.equal(rowCount(advance(600000)), 1, "running does not expire based on age");
fixture([done("background", "succeeded", serverStart + elapsed)]); render();
document.hidden = true;
elapsed += 30000; // Browser timers may be throttled while hidden.
document.hidden = false; documentListeners.get("visibilitychange")();
assert.equal(render(), null, "returning to a hidden tab immediately excludes stale completion notices");
effects.forEach(effect => effect?.cleanup?.()); states.length = 0; effects.length = 0;
assert.equal(render(), null, "a refreshed/remounted page must not show the old completion again");
effects.forEach(effect => effect?.cleanup?.());
console.log("Task banner: real expiry timer, independent terminal lifetimes, poll/refresh non-revival, active persistence and hidden-tab recovery passed");
