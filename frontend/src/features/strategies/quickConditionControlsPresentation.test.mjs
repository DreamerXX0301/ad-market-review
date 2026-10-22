/** The shortcut is an explicit draft insertion, never an active condition picker. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const slots = [];
let cursor = 0;
globalThis.__quickConditionHooks = {
  useState(initial) {
    const at = cursor++;
    slots[at] ||= { value: typeof initial === "function" ? initial() : initial };
    return [slots[at].value, next => { slots[at].value = typeof next === "function" ? next(slots[at].value) : next; }];
  },
};
const encoded = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const reactUrl = encoded(`export * from ${JSON.stringify(import.meta.resolve("react"))}; export const useState = globalThis.__quickConditionHooks.useState;`);
const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    fileName: path.pathname,
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  if (path.pathname.endsWith("RuleTreeEditor.tsx")) source += "\nexport { TechnicalPresetControls };";
  for (const match of [...source.matchAll(/(?:from |import )(["'])([^"']+)\1/g)]) {
    const name = match[2];
    if (name.endsWith(".css")) { source = source.replace(match[0], "void " + JSON.stringify(name)); continue; }
    let target;
    if (name === "react") target = reactUrl;
    else if (name.endsWith("TopicSelect")) target = "data:text/javascript,export default function TopicSelect(){return null}";
    else if (name.startsWith(".")) {
      const local = new URL(name + ".ts", path);
      try { await fs.access(local); } catch { local.pathname += "x"; }
      target = await moduleUrl(local);
    } else target = import.meta.resolve(name);
    source = source.replace(match[0], match[0].replace(match[1] + name + match[1], JSON.stringify(target)));
  }
  const url = encoded(source); modules.set(path.href, url); return url;
}
const { TechnicalPresetControls } = await import(await moduleUrl(new URL("./RuleTreeEditor.tsx", import.meta.url)));
const fields = ["change_pct", "macd_cross", "macd_dead_cross", "macd_dif", "macd_dea", "kd_cross", "kd_dead_cross", "kdj_k", "kdj_d"].map(field => ({
  field, type: field.endsWith("cross") ? "boolean" : "number",
  operators: ["==", "!=", ">", ">=", "<", "<="], supported_periods: ["D", "W", "M"], historical_event_supported: true,
}));
function nodes(element) {
  if (!element || typeof element !== "object") return [];
  return [element, ...[element.props?.children].flat(Infinity).flatMap(nodes)];
}
function text(element) {
  if (typeof element === "string" || typeof element === "number") return String(element);
  if (!element || typeof element !== "object") return "";
  return [element.props?.children].flat(Infinity).map(child => text(child)).join("");
}
function picker(view) { return nodes(view).find(node => node.props?.["aria-label"] === "快捷条件模板"); }
function input(view, name) { return nodes(view).find(node => node.props?.["aria-label"] === name); }
function button(view, name) {
  const found = nodes(view).find(node => node.type === "button" && text(node).trim() === name);
  assert(found, `missing button ${name}`); return found;
}
for (const period of ["D", "W", "M"]) {
  slots.splice(0); let calls = 0;
  const old = Object.freeze({ any: Object.freeze([Object.freeze({ field: "change_pct", op: ">", value: 2, period })]) });
  const original = JSON.stringify(old);
  const props = { value: old, rootValue: old, fields, period, depth: 0, scope: "trade-exit", onChange(next) { calls++; props.value = next; props.rootValue = next; } };
  const render = () => { cursor = 0; return TechnicalPresetControls(props); };
  let view = render();
  assert.equal(button(view, "快捷添加条件").props["aria-expanded"], false);
  assert.equal(picker(view), undefined, "closed shortcuts do not display a selected but inactive template");
  button(view, "快捷添加条件").props.onClick(); view = render();
  assert.equal(picker(view).props.value, "", "nothing is preselected");
  assert.equal(button(view, "加入条件").props.disabled, true);
  assert.match(text(view), /选择模板不会自动生效/);
  const options = nodes(picker(view)).filter(node => node.type === "option");
  assert.equal(options[1].props.value, "macd-dead-cross", "sell list still prioritizes real death-cross templates");
  picker(view).props.onChange({ target: { value: "macd-above-zero-dead" } }); view = render();
  assert.equal(calls, 0, "selection must not mutate the draft");
  assert.match(text(view), /将加入本组/);
  button(view, "取消添加").props.onClick(); view = render();
  assert.equal(calls, 0, "cancelling never changes existing rules");
  button(view, "快捷添加条件").props.onClick(); view = render();
  picker(view).props.onChange({ target: { value: "macd-above-zero-dead" } }); view = render();
  button(view, "加入条件").props.onClick(); view = render();
  assert.equal(calls, 1, "confirm adds exactly once");
  assert.equal(picker(view), undefined, "successful insert collapses the panel");
  assert.match(text(view), /已加入“MACD 水上死叉”，保存策略后生效/);
  assert.equal(props.value.any.length, 2);
  assert.equal(props.value.any[1].all.length, 3, "AND combination remains one alternative in an OR group");
  assert(props.value.any[1].all.every(leaf => leaf.period === period));
  assert.equal(JSON.stringify(old), original, "saved tree stays immutable");
  props.value = { any: props.value.any.slice(0, 1) }; props.rootValue = props.value; view = render();
  assert.doesNotMatch(text(view), /已加入/, "editing/removing that draft invalidates stale success feedback");
  button(view, "快捷添加条件").props.onClick(); view = render();
  picker(view).props.onChange({ target: { value: "kd-cross-range" } }); view = render();
  assert.match(text(view), /边界含等号，20与80可重合/);
  assert.match(text(view), /只限制 K，不限制 D/);
  assert.match(text(view), /原9、3、3算法/);
  for (const [label, min, max] of [["低位 0—20", 0, 20], ["中位 20—80", 20, 80], ["高位 80—100", 80, 100]]) {
    button(view, label).props.onClick(); view = render();
    assert.equal(input(view, "K值范围下限").props.value, min);
    assert.equal(input(view, "K值范围上限").props.value, max);
    assert.equal(calls, 1, "range starting points only configure the pending template");
  }
  input(view, "K值范围下限").props.onChange({ target: { value: "90" } });
  input(view, "K值范围上限").props.onChange({ target: { value: "30" } }); view = render();
  assert.equal(calls, 1, "editing parameters does not add a condition");
  assert.equal(button(view, "加入条件").props.disabled, true);
  button(view, "加入条件").props.onClick(); assert.equal(calls, 1, "invalid input cannot insert even via a stale click");
  input(view, "K值范围下限").props.onChange({ target: { value: "30" } });
  input(view, "K值范围上限").props.onChange({ target: { value: "80" } }); view = render();
  button(view, "加入条件").props.onClick(); view = render();
  assert.equal(calls, 2);
  assert.deepEqual(props.value.any.at(-1).all.map(leaf => [leaf.field, leaf.op, leaf.value, leaf.period]), [["kd_cross", "==", true, period], ["kdj_k", ">=", 30, period], ["kdj_k", "<=", 80, period]]);
}
console.log("quick condition workflow: collapsed by default, no preselection, selection/cancel/parameters do not mutate, explicit atomic insertion and scoped feedback across D/W/M passed");
