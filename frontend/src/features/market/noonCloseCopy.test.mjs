/** Actual close-review click handler; isolated clipboard records only completed writes. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encoded = text => "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const states = [], refs = [], effects = [], scheduled = [];
let cursor = 0, paths = [], pending = [], writes = [], clipboardError = null;
const equal = (a,b) => a?.length === b?.length && a.every((v,i) => Object.is(v,b[i]));
globalThis.__closeCopyHooks = {
 useState(initial) { const i=cursor++; if(!(i in states))states[i]=typeof initial === "function"?initial():initial; return [states[i],v=>{states[i]=typeof v==="function"?v(states[i]):v;}]; },
 useRef(initial) { const i=cursor++;return refs[i]||=( {current:initial} ); },
 useEffect(fn,deps) {const i=cursor++;if(!effects[i]||!equal(effects[i].deps,deps))scheduled.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
};
const day="2026-10-28";
let view={date:day,state:"available",snapshot_id:"frozen-noon",review:{review_id:"frozen-review",version:"noon-close-v1",revision:1,summary:{metrics:{}},classification_counts:{industries:[],topics:[]},observations:[],tomorrow:{groups:[]},gaps:[]}};
globalThis.__closeCopyApi={
 api(path){paths.push(path);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
 usePageActive(){return true;},
 useApi(path){return {data:path?.includes("/items?")?{review_id:view.review?.review_id,items:[{instrument_id:"000001.SZ",name:"合成股",noon_state:"limit_up",close_state:"limit_up",transition:"kept"}],total:60,scope_total:600,next_cursor:"frozen-next-page"}:view,loading:false,error:null,reload(){}};},
};
const savedNavigator=Object.getOwnPropertyDescriptor(globalThis,"navigator"), savedWindow=globalThis.window;
Object.defineProperty(globalThis,"navigator",{configurable:true,value:{clipboard:{async writeText(text){if(clipboardError)throw clipboardError;writes.push(text);}}}});
globalThis.window={addEventListener(){},removeEventListener(){}};
const react=encoded("export const {useState,useEffect,useRef}=globalThis.__closeCopyHooks;");
const jsx=encoded("export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const Fragment='fragment';");
const api=encoded("export const {api,useApi,usePageActive}=globalThis.__closeCopyApi;");
const icons=encoded("export const ArrowRight='arrow',ChevronDown='down',ChevronUp='up',Copy='copy',RefreshCw='refresh';");
const ui=encoded("export const Badge='badge',Empty='empty',ErrorBox='error',Loading='loading',Panel='panel';");
const board=encoded("export default 'board-filter';export const NoonBoardTag='board-tag';");
async function compile(path,jsx=false){return ts.transpileModule(await fs.readFile(new URL(path,import.meta.url),"utf8"),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022,...(jsx?{jsx:ts.JsxEmit.ReactJSX}:{})}}).outputText;}
const visibility=encoded(await compile("../../lib/indicatorVisibility.ts"));
const format=encoded((await compile("../../lib/format.ts")).replaceAll(JSON.stringify("./indicatorVisibility"),JSON.stringify(visibility))), helper=encoded(await compile("./noonClosePresentation.ts"));
let source=await compile("./NoonCloseReview.tsx",true);
for(const [from,to] of Object.entries({"react/jsx-runtime":jsx,react,"lucide-react":icons,"../../lib/api":api,"../../components/UI":ui,"../../components/CopyStockCode":encoded("export default 'stock-code';"),"../../components/FloatingScrollControls":encoded("export default 'floating-scroll-controls';"),"../../lib/format":format,"./NoonBoardFilter":board,"./noonClosePresentation":helper,"./NoonStockChange":encoded("export default 'stock-change';"),"./NoonCloseFocusTable":encoded("export default 'focus-table';")}))source=source.replaceAll(JSON.stringify(from),JSON.stringify(to));
source=source.replace('import "./NoonCloseReview.css";',"");
const {default:CloseReview}=await import(encoded(source));
const render=(date=day)=>{cursor=0;const result=CloseReview({date});scheduled.splice(0).forEach(fn=>fn());return result;};
const allNodes=(value)=>{if(!value||typeof value!=="object")return [];if(Array.isArray(value))return value.flatMap(allNodes);return [value,...Object.values(value.props||{}).flatMap(allNodes)];};
const text=value=>typeof value==="string"?value:Array.isArray(value)?value.map(text).join(""):value&&typeof value==="object"?text(value.props?.children):"";
const button=(tree,label)=>allNodes(tree).find(n=>n.type==="button"&&text(n).includes(label));
const copy=tree=>allNodes(tree).find(n=>n.props?.actions&&text(n.props.actions).includes("复制当前范围代码")).props.actions.props.onClick();
try{
 let tree=render();
 button(tree,"炸板回封").props.onClick();tree=render();
 allNodes(tree).find(n=>n.type==="board-filter").props.onChange("star");tree=render();
 allNodes(tree).find(n=>n.props?.onSort).props.onSort("close_change_pct");tree=render();
 button(tree,"下一页").props.onClick();tree=render();
 const task=copy(tree), url=new URL(paths.at(-1),"http://fixture.invalid");
 assert.equal(url.searchParams.get("codes_only"),"true");
 assert.equal(url.searchParams.get("review_id"),"frozen-review");
 assert.equal(url.searchParams.get("date"),day);
 assert.equal(url.searchParams.get("transition"),"resealed");
 assert.equal(url.searchParams.get("board"),"star");
 assert.equal(url.searchParams.get("sort"),"close_change_pct");
 assert.equal(url.searchParams.get("direction"),"desc");
 assert.equal(url.searchParams.has("cursor"),false,"copy must read complete filter, never the displayed page cursor");
 const codes=Array.from({length:60},(_,i)=>String(i+1).padStart(6,"0"));
 pending.shift().resolve({review_id:"frozen-review",codes,total:60});await task;
 assert.equal(writes[0],codes.join("\n"));assert.equal(writes[0].split("\n").length,60);
 assert.ok(text(render()).includes("已复制六位代码"));
 // No records: no clipboard call and no false success.
 button(render(),"全部").props.onClick();tree=render();let emptyTask=copy(tree);
 pending.shift().resolve({review_id:"frozen-review",codes:[],total:0});await emptyTask;
 assert.equal(writes.length,1);assert.ok(text(render()).includes("当前范围无可复制代码"));
 assert.ok(!text(render()).includes("已复制六位代码"));
 // Revision mismatch and clipboard rejection preserve actual failure.
 let invalidTask=copy(render());pending.shift().resolve({review_id:"other-review",codes:["600519"],total:1});await invalidTask;
 assert.equal(writes.length,1);assert.ok(text(render()).includes("当前核验版本不一致"));
 clipboardError=Error("fixture denied");let deniedTask=copy(render());pending.shift().resolve({review_id:"frozen-review",codes:["600519"],total:1});await deniedTask;
 assert.equal(writes.length,1);assert.ok(text(render()).includes("fixture denied"));clipboardError=null;
 // An old pending response after filter switch cannot write or claim success.
 let staleTask=copy(render());button(render(),"炸板回封").props.onClick();render();
 pending.shift().resolve({review_id:"frozen-review",codes:["600519"],total:1});await staleTask;
 assert.equal(writes.length,1);assert.ok(!text(render()).includes("已复制六位代码"));
 console.log("Actual close-review copy: fixed revision, complete 60-code filtered scope, clipboard payload, empty/mismatch/rejected/stale response paths passed");
}finally{
 effects.forEach(effect=>effect?.cleanup?.());globalThis.window=savedWindow;
 if(savedNavigator)Object.defineProperty(globalThis,"navigator",savedNavigator);else delete globalThis.navigator;
 delete globalThis.__closeCopyHooks;delete globalThis.__closeCopyApi;
}
