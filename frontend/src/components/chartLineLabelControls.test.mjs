import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, jsx = false) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined } }).outputText;
let states = [], refs = [], effects = [], cursor = 0, scheduled = [];
const same = (a, b) => a?.length === b?.length && a.every((value, index) => Object.is(value, b[index]));
globalThis.__chartLineHooks = {
  useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
  useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; },
  useEffect(effect, deps) { const index = cursor++; if (!effects[index] || !same(effects[index].deps, deps)) scheduled.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }); },
};
const observers = [];
globalThis.ResizeObserver = class { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() { this.closed = true; } };
const react = encode("export const {useState,useEffect,useRef}=globalThis.__chartLineHooks;");
const helpers = encode(compile(await fs.readFile(new URL("./chartLineLabelLayout.ts", import.meta.url), "utf8")));
let source = compile(await fs.readFile(new URL("./ChartLineLabels.tsx", import.meta.url), "utf8"), true);
source = source.replaceAll('"react"', JSON.stringify(react)).replaceAll('"./chartLineLabelLayout"', JSON.stringify(helpers)).replaceAll('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime"))).replace(/import "\.\/ChartLineLabels.css";/g, "");
const { ChartLineControls, ChartLineRail } = await import(encode(source));
const flatten = value => Array.isArray(value) ? value.flatMap(flatten) : value && typeof value === "object" && value.props ? [value, ...flatten(value.props.children)] : [];
const text = value => Array.isArray(value) ? value.map(text).join("") : value && typeof value === "object" && value.props ? text(value.props.children) : value == null ? "" : String(value);
const lines = [{ id: "one", kind: "support", group: "annotation", price: 12.51, color: "green", enabled: true, detail: "支撑原名称 · 来源A · 原锚2026-10-20" }, { id: "two", kind: "event", group: "history", price: 12.98, color: "amber", enabled: true, detail: "冻结倍量事件 · 2026-10-08 · 收盘12.98" }];
const before = structuredClone(lines), calls = [];
cursor = 0;
const controls = ChartLineControls({ lines, visible: [lines[0]], open: true, onOpen: value => calls.push(["open", value]), onToggle: (...args) => calls.push(["single", ...args]), onAll: value => calls.push(["all", value]) });
const nodes = flatten(controls), inputs = nodes.filter(node => node.type === "input");
assert.equal(inputs.length, 3); assert.equal(inputs[0].props["aria-checked"], "mixed");
inputs[0].props.onChange();
inputs[1].props.onChange({ target: { checked: false } });
inputs[2].props.onChange({ target: { checked: true } });
nodes.find(node => node.type === "button" && text(node) === "全部隐藏").props.onClick();
assert.deepEqual(calls, [["all", true], ["single", "one", false], ["single", "two", true], ["all", false]]);
assert.deepEqual(lines, before, "all visibility controls leave saved inputs unchanged");

states = []; refs = []; effects = []; scheduled = [];
let shift = 0, disposed = false;
const events = new Map();
const chart = { isDisposed: () => disposed, getOption: () => ({ yAxis: [{}] }), getDom: () => ({}), convertToPixel: (_, price) => 300 - price * 10 + shift,
  on: (name, callback) => events.set(name, callback), off: name => events.delete(name) };
const render = () => { cursor = 0; const result = ChartLineRail({ chart, lines, top: 24, height: 220, width: 112, onMore: () => calls.push(["more"]) }); const pending = scheduled; scheduled = []; pending.forEach(effect => effect()); return result; };
render(); let rail = render();
let buttons = flatten(rail).filter(node => node.props.className === "chart-line-short-label");
assert.equal(buttons.length, 2);
assert.ok(buttons.every(button => button.props.title && button.props["aria-label"]));
const firstTop = buttons[0].props.style.top;
buttons[0].props.onMouseEnter(); rail = render();
assert.ok(flatten(rail).find(node => node.props.role === "status"));
buttons[0].props.onMouseLeave(); rail = render();
assert.equal(flatten(rail).some(node => node.props.role === "status"), false);
buttons[0].props.onClick(); rail = render();
assert.ok(flatten(rail).find(node => node.props.role === "status"), "click pins the full explanation after hover leaves");
rail.props.onKeyDown({ key: "Escape", stopPropagation() { calls.push(["escape"]); } }); rail = render();
assert.equal(flatten(rail).some(node => node.props.role === "status"), false);
shift = -50; events.get("datazoom")(); rail = render();
buttons = flatten(rail).filter(node => node.props.className === "chart-line-short-label");
assert.notEqual(buttons[0].props.style.top, firstTop, "rail follows actual y-axis zoom coordinates");
shift = 15; observers.at(-1).callback(); rail = render();
buttons = flatten(rail).filter(node => node.props.className === "chart-line-short-label");
assert.notEqual(buttons[0].props.style.top, firstTop, "resize repositions labels without changing dataZoom");
assert.deepEqual(lines, before);
disposed = true; effects.forEach(effect => effect?.cleanup?.());
assert.ok(observers.at(-1).closed);

// The existing drawing/reminder panel receives display state without writing
// an annotation, cancelling an alert or changing a saved rule.
const drawingSource = await fs.readFile(new URL("./ChartDrawingTools.tsx", import.meta.url), "utf8");
const drawingAst = ts.createSourceFile("ChartDrawingTools.tsx", drawingSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const panelNode = drawingAst.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "ChartAnnotationPanel");
const runtime = await import("react/jsx-runtime");
const annotationHelpers = await import(encode(compile(await fs.readFile(new URL("./chartAnnotations.ts", import.meta.url), "utf8"))));
const panelScope = new Proxy({ _jsx: runtime.jsx, _jsxs: runtime.jsxs, ...annotationHelpers, useId: () => "line-fixture", num: value => String(value) }, {
  has: () => true,
  get: (target, key) => key === Symbol.unscopables ? undefined : key in target ? target[key] : key in globalThis ? globalThis[key] : () => null,
});
const panelCode = compile(panelNode.getText(drawingAst).replace("export function", "function"), true).replace(/^import .+;\n/gm, "").replace(/export \{\};?\n?/g, "");
const Panel = new Function("scope", `with(scope){${panelCode};return ChartAnnotationPanel;}`)(panelScope);
let mutations = 0;
const nope = () => { mutations++; throw Error("visibility cannot mutate a source or reminder"); };
const saved = { annotation_id: "saved", instrument_id: "600001.SH", line_type: "support", label: "原支撑", price: 12.51, price_basis: "none", anchor_date: null, color: "green", alert_modes: ["support_reclaim"], auto_watchlist: true };
const settings = annotationHelpers.newLineSettings();
const state = { enabled: true, open: true, scope: { price_basis: "none", anchor_date: null }, settings, isWatchlisted: false,
  show: true, drawing: false, price: "12.51", label: "", color: "green", editing: null, busy: false, error: "", isIndex: false,
  lookback: 120, preview: null, selectedCandidates: [], defaults: annotationHelpers.newLineSettings,
  result: { loading: false, error: "", reload: nope }, lines: [saved], views: [{ line: saved, price: 12.51, converted: false, reason: "同一价格口径" }],
  setSettings: nope, setShow: nope, setOpen: nope, setDrawing: nope, setEditing: nope, setPrice: nope, setLabel: nope, setColor: nope,
  save: nope, mutate: nope, calculateLevels: nope, saveLevels: nope, setLookback: nope, setPreview: nope, setSelectedCandidates: nope };
const sourceSnapshot = structuredClone(saved), policySnapshot = structuredClone(settings), displayCalls = [];
const hiddenPanel = Panel({ state, displayedIds: new Set(), onDisplayChange: (...args) => displayCalls.push(args), onDisplayAllChange: value => displayCalls.push(["all", value]) });
assert.match(text(hiddenPanel), /已隐藏/);
assert.doesNotMatch(text(hiddenPanel), /本图可见/);
flatten(hiddenPanel).find(node => node.type === "input" && node.props["aria-label"] === "显示水平线 原支撑").props.onChange({ target: { checked: true } });
flatten(hiddenPanel).find(node => node.type === "input" && "aria-checked" in node.props).props.onChange({ target: { checked: true } });
assert.deepEqual(displayCalls, [["saved", true], ["all", true]]);
const shownPanel = Panel({ state, displayedIds: new Set(["saved"]), onDisplayChange: nope, onDisplayAllChange: nope });
assert.match(text(shownPanel), /本图可见/);
assert.equal(mutations, 0); assert.deepEqual(saved, sourceSnapshot); assert.deepEqual(settings, policySnapshot);
console.log("Chart overlay controls/rail: partial/all and per-line switches, private inputs, hover/click/keyboard explanations, real-coordinate zoom/resize and cleanup passed");
