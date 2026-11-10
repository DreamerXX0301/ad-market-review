import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe =
  encode(`import * as React from ${JSON.stringify(import.meta.resolve("react"))};
export * from ${JSON.stringify(import.meta.resolve("react"))};
let scope='latest';export const changes=[];
export function setScope(value){scope=value;changes.length=0;}
export function useState(initial){const [value]=React.useState(initial==='latest'?scope:initial);return [value,(next)=>changes.push(next)];}`);
const apiProbe = encode(`let data=null;export const requests=[];
export function setData(value){data=value;requests.length=0;}
export function useApi(path){requests.push(path);return {data,loading:false,error:'',reload(){throw Error('no automatic request');}};}
export const list=value=>Array.isArray(value?.items)?value.items:[];`);
const jsxProbe =
  encode(`import {jsx as base,jsxs as bases} from ${JSON.stringify(runtime)};
export {Fragment} from ${JSON.stringify(runtime)};export const selectors=[];
function observe(type,props){if(type==='select')selectors.push(props);}
export function jsx(type,props,key){observe(type,props);return base(type,props,key);}
export function jsxs(type,props,key){observe(type,props);return bases(type,props,key);}`);
const uiProbe =
  encode(`import {jsx} from ${JSON.stringify(runtime)};export const tables=[];
export const Badge=({children})=>jsx('span',{children});
export const Panel=({title,subtitle,children,actions})=>jsx('section',{children:[jsx('h2',{children:title},'title'),jsx('p',{children:subtitle},'subtitle'),jsx('div',{children},'body'),jsx('div',{children:actions},'actions')]});
export const ErrorBox=({message})=>jsx('p',{children:message});
export const Warnings=({items})=>jsx('p',{children:items?.join('；')});
export const Loading=()=>jsx('p',{children:'读取中'});
export function Table(props){tables.push(props);return jsx('div',{children:props.rows.length?props.rows.map(row=>row.name).join(','):props.emptyTitle+'；'+props.emptyDescription});}`);
const prefetchProbe = encode(
  "export let last; export function useHistoricalPrefetch(...args){last=args;}",
);
const replacements = {
  react: reactProbe,
  "react/jsx-runtime": jsxProbe,
  "../../lib/api": apiProbe,
  "../../components/UI": uiProbe,
  "./MarketDataCoverage": encode(
    "export default function Coverage(){return null;}",
  ),
  "./useHistoricalPrefetch": prefetchProbe,
};
const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)]) {
    const name = match[2];
    const target =
      replacements[name] ||
      (name.startsWith(".")
        ? await moduleUrl(new URL(name + ".ts", path))
        : import.meta.resolve(name));
    source = source.replace(match[0], "from " + JSON.stringify(target));
  }
  const url = encode(source);
  modules.set(path.href, url);
  return url;
}
const {
  default: Monitor,
  monitorRequest,
  monitorQuoteDate,
} = await import(
  await moduleUrl(new URL("./AbnormalMonitor.tsx", import.meta.url))
);
const { setScope, changes } = await import(reactProbe);
const { setData, requests } = await import(apiProbe);
const { selectors } = await import(jsxProbe);
const { tables } = await import(uiProbe);
const prefetch = await import(prefetchProbe);
assert.equal(
  monitorRequest("2026-10-23", "latest"),
  "/market/abnormal?monitor_scope=latest&limit=1000&date=2026-10-23",
);
assert.equal(monitorRequest("", "historical"), null);
for (const date of [
  null,
  "",
  "2026-02-30",
  "20261023",
  "unknown",
  "2026-10-24T09:00:00+08:00",
])
  assert.equal(monitorQuoteDate(date), undefined);
assert.equal(monitorQuoteDate("2026-10-23"), "2026-10-23");
const selected = [];
const props = { date: "2026-10-19", onStock: (...args) => selected.push(args) };
setData({
  monitor_scope: "latest",
  requested_date: props.date,
  monitor_observed_date: "2026-10-24",
  monitor_fetched_at: "2026-10-24T09:30:00+08:00",
  quote_as_of: "2026-10-23",
  source: "fuyao",
  data_coverage: { selected_date_available: true },
  total: 1,
  warnings: ["合成来源覆盖说明"],
  items: [{ name: "合成监控股票", instrument_id: "600001.SH", code: "600001" }],
});
const current = renderToStaticMarkup(createElement(Monitor, props));
assert.equal(
  requests[0],
  "/market/abnormal?monitor_scope=latest&limit=1000&date=2026-10-19",
);
assert.equal(
  selectors.at(-1).value,
  "latest",
  "UI defaults to explicit current observation while the API default remains history",
);
assert.match(current, /名单日期 2026-10-24/);
assert.match(current, /名单获取时间[^]*2026-10-24T09:30:00\+08:00/);
assert.match(current, /行情日期 2026-10-23/);
assert.match(current, /不代表 2026-10-19 的历史监管状态/);
assert.match(current, /官方解除日期未知/);
assert.equal(
  (current.match(/<section>/g) || []).length,
  1,
  "the monitor list is the only panel; source metadata has no separate card",
);
assert.match(current, /<details class="detail-list"><summary>来源与日期说明/);
assert.doesNotMatch(current, /<details[^>]*\bopen\b/);
const visible = current.replace(/<details\b[^]*?<\/details>/g, "");
assert.match(visible, /名单日期 2026-10-24/);
assert.match(visible, /行情日期 2026-10-23/);
assert.match(visible, /1 条/);
assert.match(visible, /非盘面选择日名单/);
assert.doesNotMatch(
  visible,
  /名单获取时间|2026-10-24T09:30:00|fuyao|合成来源覆盖说明|section-intro/,
  "technical source details stay collapsed; date freshness remains visible",
);
tables.at(-1).onStockNavigate("600001.SH", tables.at(-1).rows);
assert.equal(
  selected.at(-1)[1].asOf,
  "2026-10-23",
  "latest monitor cannot open collection day or old shared view date charts",
);
selectors.at(-1).onChange({ target: { value: "historical" } });
assert.deepEqual(changes, ["historical", ""]);

setScope("historical");
setData({
  monitor_scope: "historical",
  quote_as_of: props.date,
  items: [],
  total: 0,
  data_coverage: { selected_date_available: false },
  unavailable_reason: "该日监管监控名单未留存，不能用最新名单回填历史",
});
const missing = renderToStaticMarkup(createElement(Monitor, props));
assert.equal(
  requests[0],
  "/market/abnormal?monitor_scope=historical&limit=1000&date=2026-10-19",
);
assert.equal(selectors.at(-1).value, "historical");
assert.match(missing, /名单未知/);
assert.match(missing, /名单日期 未知/);
assert.match(missing, /监控状态未知/);
assert.doesNotMatch(missing, /0 条|非盘面选择日名单/);
assert.match(missing, /不能用最新名单回填历史/);
assert.equal(prefetch.last[0], false, "missing archives do not start prefetch");

setScope("latest");
setData({
  date: props.date,
  snapshot_id: "latest-market",
  monitor_scope: "latest",
  data_coverage: { selected_date_available: true },
  items: [],
});
const prefetchDates = ["2026-10-16", props.date];
renderToStaticMarkup(
  createElement(Monitor, { ...props, latestDate: props.date, prefetchDates }),
);
assert.equal(requests[0], monitorRequest(props.date, "latest"));
assert.deepEqual(prefetch.last, [
  true,
  monitorRequest(props.date, "historical"),
  props.date,
  "latest-market",
  prefetchDates,
]);

setScope("latest");
setData({
  monitor_scope: "latest",
  monitor_observed_date: "2026-10-24",
  items: [],
  total: 0,
  data_coverage: { selected_date_available: true },
});
const empty = renderToStaticMarkup(createElement(Monitor, props));
assert.match(empty, /该观察日名单为空/);
assert.match(empty, /0 条/);
assert.match(empty, /行情日期 未知/);
assert.equal(
  tables.at(-1).onStockNavigate,
  undefined,
  "unknown quote dates cannot fall back to shared historical dates",
);
setData({
  monitor_scope: "latest",
  monitor_observed_date: props.date,
  quote_as_of: props.date,
  items: [],
  total: 0,
  data_coverage: { selected_date_available: true },
});
const sameDate = renderToStaticMarkup(createElement(Monitor, props));
assert.doesNotMatch(sameDate, /非盘面选择日名单/);
setData({
  monitor_scope: "latest",
  monitor_observed_date: "2026-02-30",
  quote_as_of: "2026-02-30",
  items: [],
  data_coverage: { selected_date_available: false },
});
const invalidDate = renderToStaticMarkup(createElement(Monitor, props));
assert.match(invalidDate, /名单日期 未知/);
assert.match(invalidDate, /行情日期 未知/);
assert.doesNotMatch(invalidDate, /2026-02-30|非盘面选择日名单/);
setData({
  items: [{ name: "旧服务历史名单不可冒充最新", instrument_id: "600001.SH" }],
  total: 1,
  data_coverage: { selected_date_available: true },
});
const incompatible = renderToStaticMarkup(createElement(Monitor, props));
assert.match(incompatible, /当前API未返回所选监控名单范围/);
assert.doesNotMatch(incompatible, /旧服务历史名单不可冒充最新/);
assert.match(incompatible, /监控状态未知/);
console.log(
  "Abnormal monitor: one compact list panel, collapsed source details, explicit latest scope, separate collection and quote dates, historical unknowns, empty source and stock navigation verified",
);
