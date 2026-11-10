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
let cursor=0,states={},simulation=null,refIndex=0,effectIndex=0;
export function setStates(value){cursor=0;states=value;}
export function useState(initial){
  const index=cursor++;
  const value=Object.hasOwn(states,index)?states[index]:initial;
  if(!simulation)return React.useState(value);
  return [value,next=>{states[index]=typeof next==="function"?next(value):next;}];
}
export function beginSimulation(value){setStates({...value});simulation={refs:[],effects:[],pending:[]};}
export function beginRender(){cursor=0;refIndex=0;effectIndex=0;}
export function useRef(initial){
  return simulation?(simulation.refs[refIndex++]??={current:initial}):React.useRef(initial);
}
export function useMemo(read,deps){return simulation?read():React.useMemo(read,deps);}
export function useLayoutEffect(effect,deps){if(!simulation)React.useLayoutEffect(effect,deps);}
export function useEffect(effect,deps){
  if(!simulation)return React.useEffect(effect,deps);
  const index=effectIndex++,previous=simulation.effects[index];
  if(previous && deps.length===previous.deps.length && deps.every((value,i)=>Object.is(value,previous.deps[i])))return;
  simulation.pending.push(()=>{previous?.cleanup?.();simulation.effects[index]={deps,cleanup:effect()};});
}
export function flushEffects(){simulation.pending.splice(0).forEach(run=>run());}
export function endSimulation(){simulation.effects.forEach(effect=>effect?.cleanup?.());simulation=null;}`);
const apiProbe = encode(`let packet=null,search=null;export const requests=[];
export function setData(value,matched){packet=value;search=matched;requests.length=0;}
export const usePageActive=()=>true;
export function useApi(path){requests.push(path);return {data:path?.startsWith('/market/dragon-tiger/search?')?search:path?.startsWith('/market/dragon-tiger?')?packet:null,loading:false,error:'',reload(){}};}
export function post(){throw Error('SSR must never create a collection task');}
export const requestKey=()=> 'synthetic-request';`);
const uiProbe = encode(`import {jsx,jsxs} from ${JSON.stringify(runtime)};
export const useToast=()=>()=>{};
export const Badge=({children})=>jsx('span',{children});
export const Status=({value})=>jsx('span',{children:value});
export const Panel=({title,subtitle,children,actions})=>jsxs('section',{children:[jsx('h2',{children:title}),jsx('p',{children:subtitle}),children,actions]});
export const Empty=({title,description,action})=>jsxs('div',{className:'empty-state',children:[jsx('h3',{children:title}),jsx('p',{children:description}),action]});
export const ErrorBox=({message})=>jsx('p',{role:'alert',children:message});
export const Loading=()=>jsx('p',{children:'读取中'});`);
const prefetchProbe = encode("export let last; export function useHistoricalPrefetch(...args){last=args;}");
const replacements = { react: reactProbe, "../../lib/api": apiProbe, "../../components/UI": uiProbe, "./useHistoricalPrefetch": prefetchProbe };
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
const { setData, requests } = await import(apiProbe);
const { setStates, beginSimulation, beginRender, flushEffects, endSimulation } = await import(reactProbe);
const prefetch = await import(prefetchProbe);
const date = "2026-01-12";
const proof = "a".repeat(64);
const emptyQuery = "无匹配合成查询";
const emptyResult = { date, day_record_sha256: proof, query: emptyQuery, available: true, matches: {} };
const a = { instrument_id: "600001.SH", code: "600001", name: "合成甲", range_days: 1, buy_value: 150, sell_value: 50, net_value: 100, label_net_value: 25 };
const b = { ...a, instrument_id: null, code: "600002", name: "合成未匹配名录" };
const groups = [{ name: "合成标签A", rows: [a, { ...a, range_days: 3 }, b] }, { name: "合成标签B", rows: [a] }];
const warning = "游资榜count/stock_count口径未核验，不按统计差额补造记录";
function packet(fields = {}, extra = {}) {
  return { date, availability: { available: true }, boards: { hot_money: { state: "partial", statistics_state: "unverified", items: groups, ...fields } }, warnings: [warning], coverage: {}, ...extra };
}
function render(data, states = {}, props = {}, search = null) {
  setData(data, search); setStates({ 0: "hot_money", ...states });
  return renderToStaticMarkup(createElement(DragonTiger, { date, onStock() {}, onNavigate() {}, ...props })).replaceAll(/<!--[^]*?-->/g, "");
}
for (const [count, stockCount] of [[0, 0], [1, 999], [52, 52], [999, 0]]) {
  const html = render(packet({ source_count: count, source_stock_count: stockCount }));
  assert.match(html, new RegExp(`来源统计 count=${count} / stock_count=${stockCount}（口径未核验）`));
  assert.match(html, /实际已观察2个标签、4条标签记录、2个来源代码去重标的、3个标的×区间/);
  assert.doesNotMatch(html, /源标的\d+个 \/ 原始记录/, "hot source metadata cannot be mislabeled as observed stock/row counts");
  assert.match(html, /已留存上榜A股<\/span><strong[^>]*>1只/, "displayed stock summary uses verified identities within the current range");
  assert.match(html, new RegExp(warning), "the unverified-statistics warning remains visible");
}
for (const states of [{ 2: emptyQuery, 13: emptyQuery }, { 3: "不存在的合成标签" }]) {
  const html = render(packet({ source_count: 52, source_stock_count: 52 }, { day_record_sha256: proof }), states, {}, emptyResult);
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
const days = ["2026-01-08", "2026-01-09", date];
const archived = packet({}, { snapshot_id: "market-current", collection_snapshot_id: "retained-day" });
render(archived, {}, { latestDate: date, prefetchDates: days });
assert.deepEqual(prefetch.last, [true, `/market/dragon-tiger?date=${date}`, date, "market-current", days]);
render(archived, {}, { latestDate: "2026-01-13", prefetchDates: days });
assert.equal(prefetch.last[0], false, "a historical date does not prefetch farther back");
render({ ...archived, availability: { available: false } }, {}, { latestDate: date, prefetchDates: days });
assert.equal(prefetch.last[0], false, "a missing archive does not authorize warming");
const all = render({ date, availability: { available: true }, boards: { all: { state: "ready", items: [a], source_count: 1, source_stock_count: 1 } } }, { 0: "all" });
assert.match(all, /源标的1个 \/ 原始记录1条/, "verified all/org source labels retain their existing explanation");
assert.doesNotMatch(all, /来源统计 count=/);
for (const board of ["all", "org"]) {
  const partial = { date, day_record_sha256: proof, availability: { available: true }, boards: { [board]: { state: "partial", items: [a, { ...a, range_days: null, source_range_days: 10 }, { ...a, range_days: null, source_range_days: 30 }], source_count: 3, source_stock_count: 1 } }, warnings: ["合成其他区间另行留存，不混入单日或三日榜"] };
  const single = render(partial, { 0: board });
  assert.match(single, /1条当前筛选记录/, "other source ranges stay out of single-day board rows");
  assert.match(single, /已留存上榜A股<\/span><strong[^>]*>1只/);
  const emptyRange = render(partial, { 0: board, 1: 3 });
  assert.match(emptyRange, /当前筛选无匹配记录/);
  assert.match(emptyRange, /0条当前筛选记录/);
  assert.match(emptyRange, /已留存上榜A股<\/span><strong[^>]*>0只/);
  assert.doesNotMatch(emptyRange, /该日龙虎榜数据尚未就绪/);
  assert.match(emptyRange, /合成其他区间另行留存/);
  const emptySearch = render(partial, { 0: board, 2: emptyQuery, 13: emptyQuery }, {}, emptyResult);
  assert.match(emptySearch, /当前筛选无匹配记录/);
  assert.match(emptySearch, /已留存上榜A股<\/span><strong[^>]*>0只/);
}
const searchDay = packet({}, { day_record_sha256: proof });
const seatQuery = "上海营业部";
const searchResult = {
  ...emptyResult, query: seatQuery,
  matches: { hot_money: [{ instrument_id: a.instrument_id, range_days: 3, label_name: "合成标签A", stock_match: null, seat_matches: [{ side: "sell", name: "合成上海营业部", record_id: "second" }] }] },
};
const settled = { 1: 3, 2: seatQuery, 13: seatQuery };
const searchHtml = render(searchDay, settled, {}, searchResult);
assert.match(searchHtml, /卖方席位 · 合成上海营业部/);
assert.match(searchHtml, /1条当前筛选记录/);
assert.deepEqual(requests.filter(Boolean), [
  `/market/dragon-tiger?date=${date}`,
  `/market/dragon-tiger/search?${new URLSearchParams({ date, query: seatQuery, archive_sha256: proof })}`,
], "one search call and no per-stock detail reads while filtering");
const otherPeriod = render(searchDay, { ...settled, 1: 1 }, {}, searchResult);
assert.match(otherPeriod, /aria-label="单日榜，0条匹配"/);
assert.match(otherPeriod, /aria-label="三日榜，1条匹配"/);
assert.match(otherPeriod, /三日榜有 1 条匹配记录/);
assert.match(otherPeriod, /查看三日榜/);
const otherLabel = render(searchDay, { ...settled, 1: 1, 3: "合成标签B" }, {}, searchResult);
assert.match(otherLabel, /aria-label="三日榜，0条匹配"/);
assert.doesNotMatch(otherLabel, /查看三日榜/);
for (const wrong of [{ day_record_sha256: "b".repeat(64) }, { date: "2026-01-09" }, { query: "前一个词" }]) {
  const stale = render(searchDay, settled, {}, { ...searchResult, ...wrong });
  assert.match(stale, /正在搜索当日已留存的股票和买卖席位/);
  assert.match(stale, /已留存上榜A股<\/span><strong[^>]*>—/);
  assert.doesNotMatch(stale, /0条当前筛选记录|合成上海营业部|dragon-tiger-period-count|查看三日榜/);
}
render(searchDay, { ...settled, 14: true }, {}, searchResult);
assert.deepEqual(requests.filter(Boolean), [`/market/dragon-tiger?date=${date}`], "IME composition does not issue an intermediate query");

function periodButton(node, name) {
  if (Array.isArray(node)) return node.map(child => periodButton(child, name)).find(Boolean);
  if (!node?.props) return;
  if (node.type === "button" && (node.props["aria-label"]?.startsWith(name + "，") || node.props.children?.[0] === name))
    return node.props;
  return periodButton(node.props.children, name);
}
function searchRender(response) {
  setData(searchDay, response);
  beginRender();
  const view = DragonTiger({ date, onStock() {}, onNavigate() {} });
  flushEffects();
  return view;
}
const initialSearch = { 0: "hot_money", 2: seatQuery, 13: seatQuery };
beginSimulation(initialSearch);
try {
  searchRender({ ...searchResult, day_record_sha256: "b".repeat(64) });
  assert.equal(periodButton(searchRender(searchResult), "单日榜")["aria-pressed"], true, "an old archive does not switch before a verified response");
  const switched = searchRender(searchResult);
  assert.equal(periodButton(switched, "三日榜")["aria-pressed"], true, "the first verified response still has its opportunity to switch");
  periodButton(switched, "单日榜").onClick();
  assert.equal(periodButton(searchRender(searchResult), "单日榜")["aria-pressed"], true);
  assert.equal(periodButton(searchRender(searchResult), "单日榜")["aria-pressed"], true, "a deliberate return to the empty period is respected on rerenders");
} finally {
  endSimulation();
}
beginSimulation(initialSearch);
try {
  periodButton(searchRender(null), "单日榜").onClick();
  searchRender(searchResult);
  assert.equal(periodButton(searchRender(searchResult), "单日榜")["aria-pressed"], true, "a manual period choice while waiting for results is respected");
} finally {
  endSimulation();
}
console.log("dragon tiger page: source observations, retained and unknown states, dated scoped search counts, one-time period switching and manual override passed");
