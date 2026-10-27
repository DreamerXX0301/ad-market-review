/** Real page JSX + React SSR; only local API and initial filter state are supplied. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = value => "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const react = import.meta.resolve("react"), runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe = encode(`import * as React from ${JSON.stringify(react)};
export * from ${JSON.stringify(react)};
let cursor=0,states={};
export function setStates(value){cursor=0;states=value;}
export function useState(initial){const index=cursor++;return React.useState(Object.hasOwn(states,index)?states[index]:initial);}`);
const apiProbe = encode(`let packet=null;export const requests=[];
export function setData(value){packet=value;requests.length=0;}
export const usePageActive=()=>true;
export function useApi(path){requests.push(path);return {data:path?.startsWith('/market/dragon-tiger?')?packet:null,loading:false,error:'',reload(){}};}
export function post(){throw Error('SSR must never create a collection task');}
export const requestKey=()=> 'synthetic-request';`);
const uiProbe = encode(`import {jsx,jsxs} from ${JSON.stringify(runtime)};
export const useToast=()=>()=>{};
export const Badge=({children})=>jsx('span',{children});
export const Status=({value})=>jsx('span',{children:value});
export const Panel=({title,subtitle,children,actions})=>jsxs('section',{children:[jsx('h2',{children:title}),jsx('p',{children:subtitle}),children,actions]});
export const Empty=({title,description})=>jsxs('div',{className:'empty-state',children:[jsx('h3',{children:title}),jsx('p',{children:description})]});
export const ErrorBox=({message})=>jsx('p',{role:'alert',children:message});
export const Loading=()=>jsx('p',{children:'读取中'});`);
const replacements = { react: reactProbe, "../../lib/api": apiProbe, "../../components/UI": uiProbe };
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let source = ts.transpileModule(await fs.readFile(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    source = source.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
    for (const name of [...new Set([...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1]))]) {
      let target = replacements[name];
      if (!target && name.startsWith(".")) {
        for (const extension of [".ts", ".tsx"]) {
          const candidate = new URL(name + extension, url);
          try { await fs.access(candidate); target = await moduleUrl(candidate); break; }
          catch (error) { if (error.code !== "ENOENT") throw error; }
        }
        if (!target) throw Error(`Missing source dependency ${name}`);
      }
      target ||= import.meta.resolve(name);
      source = source.replaceAll('from "' + name + '"', 'from ' + JSON.stringify(target)).replaceAll("from '" + name + "'", 'from ' + JSON.stringify(target));
    }
    return encode(source);
  })();
  cache.set(url.href, job);
  return job;
}
const { default: DragonTiger } = await import(await moduleUrl(new URL("./DragonTiger.tsx", import.meta.url)));
const { setData, requests } = await import(apiProbe), { setStates } = await import(reactProbe);
const date = "2026-01-12";
const a = { instrument_id: "600001.SH", code: "600001", name: "合成甲", range_days: 1, buy_value: 150, sell_value: 50, net_value: 100, label_net_value: 25 };
const b = { ...a, instrument_id: null, code: "600002", name: "合成未匹配名录" };
const groups = [{ name: "合成标签A", rows: [a, { ...a, range_days: 3 }, b] }, { name: "合成标签B", rows: [a] }];
const warning = "游资榜count/stock_count口径未核验，不按统计差额补造记录";
function packet(fields = {}, extra = {}) {
  return { date, availability: { available: true }, boards: { hot_money: { state: "partial", statistics_state: "unverified", items: groups, ...fields } }, warnings: [warning], coverage: {}, ...extra };
}
function render(data, states = {}) {
  setData(data); setStates({ 0: "hot_money", ...states });
  return renderToStaticMarkup(createElement(DragonTiger, { date, onStock() {}, onNavigate() {} })).replaceAll(/<!--[^]*?-->/g, "");
}
for (const [count, stockCount] of [[0, 0], [1, 999], [52, 52], [999, 0]]) {
  const html = render(packet({ source_count: count, source_stock_count: stockCount }));
  assert.match(html, new RegExp(`来源统计 count=${count} / stock_count=${stockCount}（口径未核验）`));
  assert.match(html, /实际已观察2个标签、4条标签记录、2个来源代码去重标的、3个标的×区间/);
  assert.doesNotMatch(html, /源标的\d+个 \/ 原始记录/, "hot source metadata cannot be mislabeled as observed stock/row counts");
  assert.match(html, /已留存上榜A股<\/span><strong[^>]*>1只/, "displayed stock summary uses verified identities within the current range");
  assert.match(html, new RegExp(warning), "the unverified-statistics warning remains visible");
}
for (const states of [{ 2: "无匹配合成查询" }, { 3: "不存在的合成标签" }]) {
  const html = render(packet({ source_count: 52, source_stock_count: 52 }), states);
  assert.match(html, /当前筛选无匹配记录/);
  assert.doesNotMatch(html, /该日龙虎榜数据尚未就绪/);
  assert.match(html, /0条当前筛选记录/);
  assert.match(html, /已留存上榜A股<\/span><strong[^>]*>0只/);
  assert.match(html, /股票龙虎榜净额（去重）<\/span><strong[^>]*>0\.00元|该标签净买额<\/span><strong[^>]*>0\.00元/);
  assert.match(html, /实际已观察2个标签、4条标签记录/, "filtering does not rewrite source observations");
  assert.match(html, new RegExp(warning));
}
const empty = render(packet({ items: [], source_count: 999, source_stock_count: 0, observed_label_count: 0, observed_label_row_count: 0, observed_stock_count: 0, observed_stock_range_count: 0 }));
assert.match(empty, /当前筛选无匹配记录/);
assert.match(empty, /实际已观察0个标签、0条标签记录、0个来源代码去重标的、0个标的×区间/);
assert.match(empty, /已留存上榜A股<\/span><strong[^>]*>0只/);
const unknown = render(packet({ state: "unknown", items: [] }, { state: "unknown", availability: { available: false, reason: "合成数据尚未留存" } }));
assert.match(unknown, /该日龙虎榜数据尚未就绪/);
assert.match(unknown, /合成数据尚未留存/);
assert.match(unknown, /已留存上榜A股<\/span><strong[^>]*>—/);
assert.match(unknown, /实际已观察—个标签、—条标签记录/);
assert.doesNotMatch(unknown, /0条当前筛选记录|当前筛选无匹配记录/);
assert.equal(requests[0], `/market/dragon-tiger?date=${date}`);
const all = render({ date, availability: { available: true }, boards: { all: { state: "ready", items: [a], source_count: 1, source_stock_count: 1 } } }, { 0: "all" });
assert.match(all, /源标的1个 \/ 原始记录1条/, "verified all/org source labels retain their existing explanation");
assert.doesNotMatch(all, /来源统计 count=/);
for (const board of ["all", "org"]) {
  const partial = { date, availability: { available: true }, boards: { [board]: { state: "partial", items: [a, { ...a, range_days: null, source_range_days: 10 }, { ...a, range_days: null, source_range_days: 30 }], source_count: 3, source_stock_count: 1 } }, warnings: ["合成其他区间另行留存，不混入单日或三日榜"] };
  const single = render(partial, { 0: board });
  assert.match(single, /1条当前筛选记录/, "other source ranges stay out of single-day board rows");
  assert.match(single, /已留存上榜A股<\/span><strong[^>]*>1只/);
  const emptyRange = render(partial, { 0: board, 1: 3 });
  assert.match(emptyRange, /当前筛选无匹配记录/);
  assert.match(emptyRange, /0条当前筛选记录/);
  assert.match(emptyRange, /已留存上榜A股<\/span><strong[^>]*>0只/);
  assert.doesNotMatch(emptyRange, /该日龙虎榜数据尚未就绪/);
  assert.match(emptyRange, /合成其他区间另行留存/);
  const emptySearch = render(partial, { 0: board, 2: "无匹配合成查询" });
  assert.match(emptySearch, /当前筛选无匹配记录/);
  assert.match(emptySearch, /已留存上榜A股<\/span><strong[^>]*>0只/);
}
console.log("dragon tiger page: unverified source metadata, legacy actual observations, valid partial empty filters, retained empty and true unknown SSR passed");
