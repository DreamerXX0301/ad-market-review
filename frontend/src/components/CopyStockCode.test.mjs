/** Controlled hooks and JSX exercise actual event/async behavior without accessing a real clipboard. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const states = [], refs = [], effects = [], pendingEffects = [], timers = new Map();
let cursor = 0, timerId = 0;
const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
globalThis.__codeHooks = {
  useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
  useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; },
  useEffect(effect, deps) { const index = cursor++; if (!effects[index] || !same(effects[index].deps, deps)) pendingEffects.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }); },
};
globalThis.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; };
globalThis.clearTimeout = id => timers.delete(id);
const writes = [], pending = [];
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: text => { writes.push(text); return new Promise((resolve, reject) => pending.push({ resolve, reject })); } } } });
const react = encode("export const {useState,useRef,useEffect}=globalThis.__codeHooks;");
const jsx = encode("export const jsx=(type,props)=>({type,props});export const jsxs=jsx;");
const icons = encode("export const Check='copy-check',Copy='copy-icon',LoaderCircle='copy-loading',TriangleAlert='copy-error';");
async function compile(path, jsx = false) { return ts.transpileModule(await fs.readFile(new URL(path, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) } }).outputText; }
const helper = encode(await compile("./stockCodeCopy.ts"));
let source = await compile("./CopyStockCode.tsx", true);
source = source.replaceAll('"react/jsx-runtime"', JSON.stringify(jsx)).replaceAll('"react"', JSON.stringify(react)).replaceAll('"lucide-react"', JSON.stringify(icons)).replaceAll('"./stockCodeCopy"', JSON.stringify(helper)).replace('import "./CopyStockCode.css";', "");
const { default: CopyStockCode } = await import(encode(source));
function render(props = { instrumentId: "000001.SZ" }) { cursor = 0; const output = CopyStockCode(props); const effects = pendingEffects.splice(0); effects.forEach(effect => effect()); return output; }
const button = tree => tree.props.children.find(child => child && child.type === "button");
const event = () => ({ stopped: false, prevented: false, stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } });
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
const tree = render();
assert.equal(tree.type, "span");
assert.equal(tree.props.children[0].type, "code");
assert.match(button(tree).props.title, /复制六位代码 000001/);
const selecting = event(); tree.props.onMouseDown(selecting);
assert.equal(selecting.stopped, true); assert.equal(selecting.prevented, false, "code text remains selectable");
const click = event(); button(tree).props.onClick(click);
assert.equal(click.stopped, true); assert.equal(click.prevented, true);
assert.deepEqual(writes, ["000001"]);
button(tree).props.onClick(event()); assert.equal(writes.length, 1, "guard duplicate clicks before React rerenders");
assert.equal(button(render()).props.disabled, true);
pending.shift().resolve(); await flush();
assert.match(button(render()).props.title, /已复制 000001/);
assert.equal(timers.size, 1);
const next = render({ instrumentId: "600519.SH" });
assert.doesNotMatch(button(next).props.title, /已复制/, "new stock must not inherit previous success feedback for one frame");
assert.equal(timers.size, 0);
button(render({ instrumentId: "600519.SH" })).props.onClick(event());
const old = pending.shift();
render({ instrumentId: "300001.SZ" });
old.resolve(); await flush();
assert.doesNotMatch(button(render({ instrumentId: "300001.SZ" })).props.title, /已复制/);
button(render({ instrumentId: "300001.SZ" })).props.onClick(event()); pending.shift().reject(Error("fixture denied")); await flush();
const failed = render({ instrumentId: "300001.SZ" });
assert.match(button(failed).props.title, /请选中代码手动复制/);
assert.equal(failed.props.children[0].props.children, "300001");
assert.equal(failed.props.children[2].props.role, "status");
const key = event(); key.key = "Enter"; button(failed).props.onKeyDown(key); assert.equal(key.stopped, true);
effects.forEach(effect => effect?.cleanup?.());
console.log("Copy code UI: keyboard/click isolation, selectable fallback, real success, duplicate guard and stock-switch race passed");
