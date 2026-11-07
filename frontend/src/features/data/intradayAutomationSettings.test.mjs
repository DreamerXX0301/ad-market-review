import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const probe = encode(
  `import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};export const controls=[];function see(t,p){if(['input','button','a'].includes(t))controls.push({type:t,...p});}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`,
);
const api = encode(
  `export function usePageActive(){return globalThis.__active;}export function useApi(){return{data:globalThis.__setting,loading:false,error:'',reload(){globalThis.__reloads++;},setData(next){globalThis.__setting=next;globalThis.__saves++;}};}export async function api(path,options){globalThis.__calls.push([path,JSON.parse(options.body)]);return globalThis.__response;}`,
);
const ui = encode(
  `import{jsx}from ${JSON.stringify(runtime)};export const Panel=({children})=>jsx('section',{children});export const Badge=({children})=>jsx('b',{children});export const ErrorBox=({message})=>jsx('p',{children:message});export const Spinner=()=>jsx('span',{children:'loading'});`,
);
async function load(file) {
  let source = ts
    .transpileModule(
      await fs.readFile(new URL(file, import.meta.url), "utf8"),
      {
        compilerOptions: {
          module: ts.ModuleKind.ES2022,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      },
    )
    .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
  const modules = {
    "../../lib/api": api,
    "../../components/UI": ui,
    "react/jsx-runtime": probe,
  };
  for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
    source = source.replace(
      match[0],
      "from " +
        JSON.stringify(modules[match[2]] || import.meta.resolve(match[2])),
    );
  return import(encode(source));
}
const {
  default: Settings,
  intradayAutomationValue,
  confirmedIntradayAutomation,
} = await load("./IntradayAutomationSettings.tsx");
const { IntradayAutomationStatus } = await load(
  "../market/IntradayAutomation.tsx",
);
const { controls } = await import(probe);
assert.equal(intradayAutomationValue({ enabled: true, revision: -1 }), null);
assert.equal(intradayAutomationValue({ enabled: "true", revision: 1 }), null);
assert.equal(
  confirmedIntradayAutomation(
    { enabled: false, revision: 3 },
    { enabled: true, revision: 3 },
  ),
  null,
);
globalThis.__active = true;
globalThis.__setting = {
  enabled: false,
  revision: 3,
  slots: [
    { start: "09:35" },
    { start: "09:45" },
    { start: "10:00" },
    { start: "14:30" },
  ],
};
globalThis.__response = { enabled: true, revision: 4 };
globalThis.__calls = [];
globalThis.__reloads = 0;
globalThis.__saves = 0;
function render() {
  controls.length = 0;
  return renderToStaticMarkup(createElement(Settings));
}
let html = render();
assert.match(html, /id="intraday-automatic-settings"/);
assert.match(html, /09:35<\/li><li>09:45<\/li><li>10:00<\/li><li>14:30/);
await controls.find((row) => row.type === "checkbox").onChange();
assert.deepEqual(globalThis.__calls, [
  ["/market/intraday/automation", { enabled: true, expected_revision: 3 }],
]);
assert.equal(globalThis.__saves, 1);
assert.equal(globalThis.__setting.enabled, true);
globalThis.__response = { enabled: true, revision: 5 };
render();
await controls.find((row) => row.type === "checkbox").onChange();
assert.equal(
  globalThis.__saves,
  1,
  "a response that did not confirm the requested toggle cannot be accepted",
);
assert.equal(globalThis.__reloads, 1);
globalThis.__active = false;
html = render();
assert.match(html, /type="checkbox" disabled="" checked=""/);
const count = globalThis.__calls.length;
await controls.find((row) => row.type === "checkbox").onChange();
assert.equal(globalThis.__calls.length, count);
controls.length = 0;
html = renderToStaticMarkup(
  createElement(IntradayAutomationStatus, { value: { enabled: true } }),
);
assert.match(html, /自动采集已开/);
assert.equal(controls.length, 1);
assert.equal(controls[0].type, "a");
assert.equal(controls[0].href, "#data?section=intraday-automatic");
assert.equal(controls[0].onClick, undefined);
assert.match(
  renderToStaticMarkup(
    createElement(IntradayAutomationStatus, { value: { enabled: false } }),
  ),
  /自动采集关闭/,
);
assert.match(
  renderToStaticMarkup(
    createElement(IntradayAutomationStatus, {
      value: { enabled: true },
      error: "preview readonly",
    }),
  ),
  /自动状态待确认/,
);
const statusSource = await fs.readFile(
  new URL("../market/IntradayAutomation.tsx", import.meta.url),
  "utf8",
);
assert.doesNotMatch(
  statusSource,
  /method:|PUT|expected_revision|onClick=/,
  "observation status cannot change authorization",
);
for (const key of [
  "__active",
  "__setting",
  "__response",
  "__calls",
  "__reloads",
  "__saves",
])
  delete globalThis[key];
console.log(
  "Intraday automation: central settings only, confirmed optimistic revision, inactive guard and read-only observation navigation passed",
);
