import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    fileName: path.pathname,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [
    ...source.matchAll(/(?:from |import )(["'])([^"']+)\1/g),
  ]) {
    const name = match[2];
    if (name.endsWith(".css")) {
      source = source.replace(match[0], "void " + JSON.stringify(name));
      continue;
    }
    let target;
    if (name.endsWith("TopicSelect"))
      target =
        "data:text/javascript,export default function TopicSelect(){return null}";
    else if (name.startsWith(".")) {
      const local = new URL(name + ".ts", path);
      try {
        await fs.access(local);
      } catch {
        local.pathname += "x";
      }
      target = await moduleUrl(local);
    } else target = import.meta.resolve(name);
    source = source.replace(
      match[0],
      match[0].replace(match[1] + name + match[1], JSON.stringify(target)),
    );
  }
  const url =
    "data:text/javascript;base64," + Buffer.from(source).toString("base64");
  modules.set(path.href, url);
  return url;
}
const {default: TradeRulesEditor} = await import(await moduleUrl(new URL("./TradeRules.tsx", import.meta.url)));
const {default: RuleTreeEditor} = await import(await moduleUrl(new URL("./RuleTreeEditor.tsx", import.meta.url)));
const fields = ["close", "macd_cross", "macd_dead_cross", "macd_dif", "macd_dea", "kd_cross", "kd_dead_cross", "kdj_k", "kdj_d"].map(field => ({
  field, type: field.endsWith("cross") ? "boolean" : "number", operators: field.endsWith("cross") ? ["==", "!="] : [">", ">=", "<", "<=", "==", "!="], supported_periods: ["D", "W", "M"],
}));
const value = Object.freeze({entry: Object.freeze({all: []}), exit: Object.freeze({all: []})});
const original = JSON.stringify(value);
const html = renderToStaticMarkup(createElement(TradeRulesEditor, {value, onChange: () => {throw new Error("SSR must not save or rewrite rules")}, fields, period: "W"}));
const exit = html.slice(html.indexOf('class="trade-rule-card exit"'));
const entry = html.slice(html.indexOf('class="trade-rule-card entry"'), html.indexOf('class="trade-rule-card exit"'));
assert.match(exit, /<option value="macd-dead-cross" selected="">MACD 死叉<\/option>/);
assert.match(entry, /<option value="macd-above-zero" selected="">MACD 水上金叉<\/option>/);
for (const label of ["MACD 死叉", "KDJ 死叉", "MACD 水上死叉", "KDJ 高位死叉", "KDJ K &lt; D（持续状态）"])
  assert(exit.includes(label), label);
assert(exit.indexOf('value="macd-dead-cross"') < exit.indexOf('value="macd-above-zero"'));
assert(exit.includes("完整周K"), "sell presets inherit chosen complete-week period");
assert.equal(JSON.stringify(value), original);
const fallback = renderToStaticMarkup(createElement(RuleTreeEditor, {value:{all:[]}, onChange:()=>{}, fields:fields.filter(x=>!["macd_dead_cross","kd_dead_cross"].includes(x.field)), period:"D", scope:"trade-exit"}));
assert(!fallback.includes('value="macd-dead-cross"'), "unavailable fields cannot become a shortcut");
console.log("sell shortcut presentation: death-first exit, preserved entry defaults, inherited period, dependency gating and no saved-rule mutation passed");
