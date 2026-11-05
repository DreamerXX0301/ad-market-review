import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = text => "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = text => ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const source = await fs.readFile(new URL("./IntradayObservation.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("IntradayObservation.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const named = name => ast.statements.find(node => node.name?.text === name || node.declarationList?.declarations.some(item=>item.name?.text===name)).getText(ast);
const hooksUrl = encode(`let slots=[],cursor=0,effects=[],pending=[];export function reset(){slots=[];effects=[];pending=[];cursor=0;}export function begin(){cursor=0;}export function flush(){pending.splice(0).forEach(fn=>fn());}export function useState(initial){const at=cursor++;if(!(at in slots))slots[at]=typeof initial==='function'?initial():initial;return[slots[at],value=>slots[at]=typeof value==='function'?value(slots[at]):value];}export function useEffect(fn,deps){const at=cursor++;if(!effects[at]||deps.some((value,i)=>value!==effects[at][i])){effects[at]=deps;pending.push(fn);}}`);
const hooks = await import(hooksUrl);
const boardUrl = encode(compile(await fs.readFile(new URL("./noonBoardPresentation.ts", import.meta.url), "utf8")));
const sortUrl = encode(compile(await fs.readFile(new URL("./marketPresentation.ts", import.meta.url), "utf8")));
const { filterRows } = await import(boardUrl), { sortMarketRows } = await import(sortUrl);
const reactUrl=import.meta.resolve("react"), runtimeUrl=import.meta.resolve("react/jsx-runtime");
const prefix = `import React from ${JSON.stringify(reactUrl)};import {useState,useEffect} from ${JSON.stringify(hooksUrl)};import {filterRows,NOON_BOARD_OPTIONS,boardLabel,boardOf} from ${JSON.stringify(boardUrl)};import {sortMarketRows} from ${JSON.stringify(sortUrl)};
const integer=v=>String(v),num=integer,amount=v=>v==null?'—':String(v),percent=amount,tone=v=>v==null?'':v<0?'down':v>0?'up':'';const delta=(v,u)=>v==null?'—':String(v)+u;
const useApi=path=>globalThis.__rankingApi(path);
const Panel=({title,subtitle,className,children})=>React.createElement('section',{className},title&&React.createElement('h2',null,title),subtitle&&React.createElement('p',null,subtitle),children);
const Empty=({title})=>React.createElement('p',null,title),Loading=()=>React.createElement('p',null,'读取中'),ErrorBox=({message})=>React.createElement('p',null,message);
const MarketSortHeader=({field,caption,onSort})=>React.createElement('button',{'data-sort':field,onClick:onSort},caption);
const ThemeChips=()=>null,IntradayStockStateBadge=()=>null,HistoricalPriceDates=()=>null;
const CopyStockCode=({instrumentId})=>React.createElement('code',null,instrumentId);
const GitCompareArrows=()=>null,Filter=()=>null;
const isStructuralLadderTopic=({name})=>name==='融资融券';
`;
function moduleCode(names, extra="") {
  return compile(prefix + extra + names.map(named).join("\n"))
    .replaceAll('"react/jsx-runtime"', JSON.stringify(runtimeUrl));
}
const { Ranking } = await import(encode(moduleCode(["stockHistoryQuery","historySortFields","intradayReturnLabel","intradayClosingChange","showIntradayClosingComparison","Ranking"])));
const rows=Array.from({length:32},(_,i)=>({instrument_id:`${i%10===0?'300':'600'}${String(i+1).padStart(3,'0')}.${i%10===0?'SZ':'SH'}`,name:`股票${i}`,change_pct:i,amount:100-i,open_change_pct:i%2?null:i}));
const values=Object.fromEntries(rows.map((row,i)=>[row.instrument_id,{change_30d_pct:i===0?999:i,return_ytd_pct:i}]));
let requests=[],server=false,frozen=false;
globalThis.__rankingApi=path=>{
  if(path) requests.push(path);
  if(!path)return {data:null,loading:false,error:"",reload(){}};
  const url=new URL(path,"http://test.invalid"),params=url.searchParams;
  let data;
  if(url.pathname.endsWith("/history")) {
    const ids=params.get("symbols").split(",");assert.ok(ids.length<=32,"each history read stays within the endpoint bound");
    data={date:params.get("date"),slot:params.get("slot"),as_of:params.get("date"),items:Object.fromEntries(ids.map(id=>[id,values[id]||{}]))};
  } else {
    assert.equal(server,true);
    const size=Number(params.get("limit")),page=Number(params.get("page"));
    const all=filterRows(rows,params.get("board"));
    const sorted=sortMarketRows(all.map(row=>({...row,...(params.get("sort")==="change_30d_pct"?values[row.instrument_id]:{})})),{key:params.get("sort"),order:params.get("direction")});
    data={...Object.fromEntries(params),page,limit:size,state:frozen?"unknown":"available",matched:all.length,total:rows.length,pages:Math.ceil(all.length/size),items:sorted.slice((page-1)*size,page*size)};
  }
  return {data,loading:false,error:"",reload(){}};
};
function nodes(value) {
  if(value==null||typeof value!=="object")return[];
  if(Array.isArray(value))return value.flatMap(nodes);
  if(typeof value.type==="function")return nodes(value.type(value.props));
  return [value,...nodes(value.props?.children)];
}
const text=value=>value==null?"":typeof value!=="object"?String(value):Array.isArray(value)?value.map(text).join(""):text(value.props?.children);
let tree;
const historyScope={date:"2026-11-05",slot:"1400"};
let props={rows,historyScope,stockMode:"candidates"};
function render(){hooks.begin();requests=[];tree=Ranking(props);hooks.flush();return renderToStaticMarkup(tree);}
const button=label=>nodes(tree).find(node=>node.type==="button"&&text(node)===label);
const rowNames=()=>nodes(nodes(tree).find(node=>node.type==="tbody")).filter(node=>node.type==="th").map(node=>text(node));
hooks.reset();let html=render();
const originalWidths=nodes(tree).filter(node=>node.type==="col").map(node=>node.props.style.width);
assert.equal(rowNames().length,10);assert.ok(rowNames()[0].startsWith("股票31"));
assert.match(html,/1–10 \/ 共 32 条/);assert.match(html,/第 1 \/ 4 页/);
assert.doesNotMatch(html,/查看已留存|收起/);
assert.equal(new URL(requests.find(path=>path.includes("/history?")),"http://test.invalid").searchParams.get("symbols").split(",").length,10);
button("下一页").props.onClick();html=render();assert.match(html,/第 2 \/ 4 页/);assert.ok(rowNames()[0].startsWith("股票21"));
nodes(tree).find(node=>node.type==="select").props.onChange({target:{value:"20"}});html=render();
assert.equal(rowNames().length,20);assert.match(html,/第 1 \/ 2 页/);
button("下一页").props.onClick();render();
nodes(tree).find(node=>node.props?.["data-sort"]==="change_30d_pct").props.onClick();html=render();
assert.match(html,/第 1 \/ 2 页/);assert.ok(rowNames()[0].startsWith("股票0"),"historical sorting considers the entire retained list before paging");
assert.equal(new URL(requests.find(path=>path.includes("/history?")),"http://test.invalid").searchParams.get("symbols").split(",").length,32);
button("创业板").props.onClick();html=render();assert.equal(rowNames().length,4);assert.match(html,/第 1 \/ 1 页/);
button("科创板").props.onClick();html=render();assert.equal(rowNames().length,0);assert.match(html,/<colgroup>/);assert.match(html,/<thead>/);assert.match(html,/0 \/ 共 0 条/);
assert.deepEqual(nodes(tree).filter(node=>node.type==="col").map(node=>node.props.style.width),originalWidths,"filtering and empty results preserve the same column geometry");
hooks.reset();server=true;props={...props,memberScope:{...historyScope,snapshotId:"sample",topicCode:"884001.TI"}};
html=render();assert.equal(new URL(requests[0],"http://test.invalid").searchParams.get("limit"),"10");
button("下一页").props.onClick();html=render();assert.match(html,/第 2 \/ 4 页/);
nodes(tree).find(node=>node.type==="select").props.onChange({target:{value:"30"}});html=render();
assert.equal(new URL(requests[0],"http://test.invalid").searchParams.get("limit"),"30");assert.equal(rowNames().length,30);assert.match(html,/第 1 \/ 2 页/);
frozen=true;html=render();assert.equal(rowNames().length,30,"unknown member coverage uses paginated retained samples rather than displaying every sample");

const changesModule=await import(encode(moduleCode(["clock","evidenceCount","feedbackThemeGroups","filterFeedbackTheme","boardChangeKinds","boardChangeReason","boardChangeThemeDistribution","BoardChangeThemes","IntradayBoardChanges"],`const Ranking=props=>{globalThis.__changeRanking=props;return React.createElement('div',{'data-ranking':true},props.rows.map(row=>row.name).join(','));};`)));
const { IntradayBoardChanges: Changes, boardChangeThemeDistribution }=changesModule;
const stocks=Array.from({length:8},(_,i)=>({instrument_id:`s${i}`,name:`样本${i}`,themes:i===7?[]:[{kind:"industry",name:`行业${i%5}`},{kind:"concept",name:`题材${i%6}`},{kind:"concept",name:"融资融券"}]}));
const snapshot={id:"one",date:"2026-11-05",slot:"1400",board_changes:{baseline_slot:"1330",groups:{new_sealed:{state:"observed",count:10,items:stocks,omitted_count:2},new_broken:{state:"observed",count:2,items:stocks.slice(0,2)}}}};
const distribution=boardChangeThemeDistribution([...stocks,stocks[0]]);
assert.equal(distribution.stocks.length,8);assert.equal(distribution.industry.ordered.length,5);assert.equal(distribution.concept.ordered.length,6);assert.equal(distribution.concept.attributes.length,1);assert.equal(distribution.concept.missing.length,1);
function renderChanges(value=snapshot){hooks.begin();tree=Changes({snapshot:value});hooks.flush();return renderToStaticMarkup(tree);}
hooks.reset();html=renderChanges();
assert.match(html,/已列 8 只股票/);assert.match(html,/另有 2 只已确认记录未列入分布/);
assert.match(html,/全部行业 · 5 个/);assert.match(html,/全部概念 · 6 个/);assert.match(html,/行业归属未核验 1 只股票/);
assert.equal(nodes(tree).filter(node=>node.type==="details").length,3,"all specific industry/concepts and general attributes remain expandable");
const category=label=>nodes(tree).find(node=>node.type==="button"&&node.props["data-kind"]&&text(node.props.children[0])===label);
category("新增封板").props.onClick();html=renderChanges();assert.doesNotMatch(html,/data-ranking|intraday-change-content/);assert.equal(nodes(tree).filter(node=>node.type==="button"&&node.props["data-kind"]&&node.props["aria-pressed"]).length,0,"cancelled category remains null rather than falling back to the first");
category("新增封板").props.onClick();renderChanges();
const themeChip=()=>nodes(tree).find(node=>node.type==="button"&&node.props.className==="intraday-feedback-leader"&&text(node).startsWith("行业0"));
themeChip().props.onClick();renderChanges();assert.equal(globalThis.__changeRanking.rows.length,2);assert.equal(globalThis.__changeRanking.columnRows.length,8,"filtering keeps full-group column definitions stable");
themeChip().props.onClick();renderChanges();assert.equal(globalThis.__changeRanking.rows.length,8,"clicking the active industry again clears only the theme filter");
category("新增炸板").props.onClick();renderChanges();assert.equal(globalThis.__changeRanking.rows.length,2);
renderChanges({...snapshot,id:"two",slot:"1430"});html=renderChanges({...snapshot,id:"two",slot:"1430"});assert.equal(category("新增封板").props["aria-pressed"],true,"a different observation resets the category");
delete globalThis.__rankingApi;delete globalThis.__changeRanking;
console.log("Intraday tables: 10/20/30 paging, full-list sorting, bounded visible history, server limits, stable empty headers, complete theme distribution and reversible filters passed");
