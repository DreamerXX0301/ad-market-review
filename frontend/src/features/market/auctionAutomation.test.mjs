import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const jsxProbe = encode(
  `import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};export const buttons=[];function see(t,p){if(t==='button'||t==='a')buttons.push({type:t,...p});}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`,
);
const ui = encode(
  `import{jsx}from ${JSON.stringify(runtime)};export const Badge=({children})=>jsx('span',{children});export const ErrorBox=({message})=>jsx('p',{children:message});`,
);
async function compile(file) {
  return ts.transpileModule(
    await fs.readFile(new URL(file, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
}
const original = await fs.readFile(
  new URL("./AuctionAutomation.tsx", import.meta.url),
  "utf8",
);
let source = await compile("./AuctionAutomation.tsx");
const imports = {
  "react/jsx-runtime": jsxProbe,
  "../../components/UI": ui,
  "./noonPresentation": encode("export const noonClock=String;"),
  "./auctionPresentation": encode(await compile("./auctionPresentation.ts")),
  "../data/automaticReviewPresentation": encode(
    await compile("../data/automaticReviewPresentation.ts"),
  ),
};
for (const m of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    m[0],
    "from " + JSON.stringify(imports[m[2]] || import.meta.resolve(m[2])),
  );
const { default: Component } = await import(encode(source));
const { buttons } = await import(jsxProbe);
const initial = {
  version: "auction-automation-v1",
  revision: 0,
  mode: "off",
  date: null,
  instrument_ids: [],
  today: "2026-10-29",
  start_time: "09:25:30",
  enabled: false,
  next_run_at: null,
  worker: { alive: false },
  last_run: null,
};
let refreshed = 0,
  saved = 0;
function render(value, props = {}) {
  buttons.length = 0;
  return renderToStaticMarkup(
    createElement(Component, {
      value,
      loading: false,
      error: "",
      reload() {
        refreshed++;
      },
      onSaved() {
        saved++;
      },
      ...props,
    }),
  );
}
let html = render(initial);
assert.match(html, /未开启每日采集/);
assert.match(html, /去数据中心设置/);
assert.doesNotMatch(html, /手动采集|单次|填写名单|保存并开启/);
const link = buttons.find((x) => x.type === "a");
assert.equal(link.href, "#data?section=automatic");
const refresh = buttons.find((x) => x.type === "button");
refresh.onClick();
assert.equal(refreshed, 1);
assert.equal(saved, 0);
assert.equal(
  buttons.length,
  2,
  "only status refresh and settings navigation remain",
);
html = render({
  ...initial,
  revision: 1,
  mode: "daily",
  enabled: true,
  worker: { alive: true },
});
assert.match(html, /服务自动竞价：已开启/);
assert.match(html, /北京时间09:25:30/);
assert.equal((html.match(/09:25:30/g) || []).length, 1);
assert.doesNotMatch(html, /固定时间|worker|工作进程/);
html = render({
  ...initial,
  reason: "唯一worker未运行",
  last_run: { reason: "工作进程已停止" },
});
assert.match(html, /采集服务未运行/);
assert.match(html, /采集服务已停止/);
assert.match(html, /服务未运行/);
assert.doesNotMatch(html, /worker|工作进程/);
html = render({ ...initial, mode: "once", enabled: true });
assert.match(
  html,
  /未开启每日采集/,
  "old once is not presented as daily authorization",
);
html = render({
  ...initial,
  mode: "daily",
  enabled: true,
  instrument_ids: ["600001.SH"],
});
assert.match(
  html,
  /未开启每日采集/,
  "old custom scope is not a current daily setting",
);
html = render({ ...initial, worker: undefined });
assert.match(html, /服务状态待确认/);
assert.doesNotMatch(html, /服务未运行/);
html = render({ ...initial, enabled: undefined });
assert.match(
  html,
  /状态待确认/,
  "incomplete enabled state is not shown as disabled",
);
html = render(null);
assert.match(html, /状态待确认/);
render(initial, { loading: true });
assert.equal(buttons.find((x) => x.type === "button").disabled, true);
html = render({
  ...initial,
  last_run: {
    date: "2026-10-29",
    state: "paused",
    reason: "原任务已暂停",
    attempts: 1,
  },
});
assert.match(html, /已暂停/);
assert.match(html, /原任务已暂停/);
assert.doesNotMatch(html, /继续采集|恢复任务/);
assert.equal(
  /\bpost\b|\bapi\s*\(|textarea|type="radio"|type="date"|type="time"|onSaved\(/.test(
    original,
  ),
  false,
  "read-only component has no mutation endpoint/editor",
);
console.log(
  "Auction automation read-only: status/unknown/legacy distinction, fixed time, settings link, GET refresh only and no editor/mutation passed",
);
