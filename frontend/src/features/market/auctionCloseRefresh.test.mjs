/** Real read hook + refresh subscription, synthetic task events and controlled GETs only. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const values=[],refs=[],callbacks=[],effects=[],requests=[],listeners=new Map();
let cursor=0,scheduled=[],open=false,hasSnapshot=true,active=true,day='2026-10-29';
const same=(a,b)=>a?.length===b?.length&&a.every((value,index)=>Object.is(value,b[index]));
const hooks={
 createContext(value){return{value};},useContext(context){return context.value;},
 useState(initial){const index=cursor++;if(!(index in values))values[index]=typeof initial==='function'?initial():initial;return[values[index],next=>{values[index]=typeof next==='function'?next(values[index]):next;}];},
 useRef(initial){return refs[cursor++]||=( {current:initial} );},
 useCallback(fn,deps){const index=cursor++;if(!callbacks[index]||!same(callbacks[index].deps,deps))callbacks[index]={fn,deps};return callbacks[index].fn;},
 useEffect(fn,deps){const index=cursor++;if(!effects[index]||!same(effects[index].deps,deps))scheduled.push(()=>{effects[index]?.cleanup?.();effects[index]={deps,cleanup:fn()};});},
};
globalThis.__auctionRefreshHooks=hooks;
globalThis.window={
 addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},
 removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
 dispatchEvent(event){for(const fn of listeners.get(event.type)||[])fn(event);},
};
globalThis.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
const encode=value=>'data:text/javascript;base64,'+Buffer.from(value).toString('base64');
const react=encode('export const {createContext,useContext,useState,useRef,useEffect,useCallback}=globalThis.__auctionRefreshHooks;');
const compile=async path=>ts.transpileModule(await fs.readFile(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const events=encode(await compile('../../lib/workbenchEvents.ts'));
let apiSource=(await compile('../../lib/api.ts')).replaceAll('"react"',JSON.stringify(react)).replaceAll('"../components/incrementSession"',JSON.stringify(encode('export function restoreIncrementSession(){throw Error("No task creation or session changes in refresh tests");}')));
const {useApi,ApiPageActivity}=await import(encode(apiSource));
const refreshSource=(await compile('./useAuctionCloseRefresh.ts')).replaceAll('"react"',JSON.stringify(react)).replaceAll('"../../lib/workbenchEvents"',JSON.stringify(events));
const {default:useRefresh}=await import(encode(refreshSource));
const {MARKET_INCREMENT_FINISHED}=await import(events);
function render(){
 cursor=0;ApiPageActivity.value=active;
 const result=useApi(open&&hasSnapshot?`/market/auction/close-review?date=${day}`:null);
 useRefresh({active,open,hasSnapshot,reload:result.reload});
 const pending=scheduled;scheduled=[];pending.forEach(fn=>fn());return result;
}
const event=type=>{window.dispatchEvent(new Event(type));return render();};
async function respond(index,status,body){requests[index].resolve({ok:status<400,status,text:async()=>JSON.stringify(body)});await new Promise(resolve=>setImmediate(resolve));return render();}

render();event(MARKET_INCREMENT_FINISHED);
assert.equal(requests.length,0,'a collapsed review never fetches on completion');
open=true;render();assert.equal(requests.length,1,'opening reads the latest published result');
await respond(0,200,{date:day,state:'pending'});
event(MARKET_INCREMENT_FINISHED);
assert.equal(requests.length,2,'a completed increment automatically rereads an already-open review');
assert.equal(requests[1].url,'/api/v1/market/auction/close-review?date=2026-10-29');
await respond(1,200,{date:day,state:'available'});
for(const type of ['workbench-increment-unlocked','workbench-noon-unlocked','workbench-auction-unlocked'])event(type);
assert.equal(requests.length,2,'ordinary unlock/noon/auction events are not increment publication');

open=false;render();event(MARKET_INCREMENT_FINISHED);assert.equal(requests.length,2);
open=true;render();assert.equal(requests.length,3,'reopening after a missed event reads once');
await respond(2,200,{date:day,state:'available'});
active=false;render();event(MARKET_INCREMENT_FINISHED);assert.equal(requests.length,3,'hidden/locked readers do not fetch');
active=true;render();assert.equal(requests.length,4,'activation reads once even when completion happened while hidden');
await respond(3,200,{date:day,state:'pending'});

hasSnapshot=false;render();event(MARKET_INCREMENT_FINISHED);assert.equal(requests.length,4,'missing or purged auction snapshots do not trigger posthoc reads');
hasSnapshot=true;render();assert.equal(requests.length,5);
day='2026-10-30';render();assert.equal(requests.length,6);
assert.equal(requests[4].options.signal.aborted,true,'switching date aborts the old reader');
await respond(4,200,{date:'2026-10-29',marker:'late-old-date'});
assert.equal(render().data,null,'a late previous-day response cannot appear in the new day');
await respond(5,200,{date:day,state:'pending'});
event(MARKET_INCREMENT_FINISHED);assert.equal(requests.length,7);
assert.equal(requests[6].url,'/api/v1/market/auction/close-review?date=2026-10-30','the completion reread uses the current view date');
await respond(6,503,{detail:'temporary read failure'});
assert.match(render().error,/temporary read failure/);
event(MARKET_INCREMENT_FINISHED);assert.equal(requests.length,8,'a later completed increment can recover a failed read without a click');
await respond(7,200,{date:day,state:'available'});assert.equal(render().error,'');
assert.ok(requests.every(request=>!request.options.method),'automatic refresh only performs GETs; it never starts collection');
effects.forEach(effect=>effect?.cleanup?.());
assert.ok([...listeners.values()].every(set=>set.size===0),'unmount removes every refresh listener');
delete globalThis.__auctionRefreshHooks;delete globalThis.window;delete globalThis.fetch;
console.log('Auction posthoc refresh: completion reads, closed/hidden gating, activation catch-up, date isolation, error recovery and GET-only behavior passed');
