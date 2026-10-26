import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const values = [], refs = [], callbacks = [], effects = [], requests = [], listeners = new Map();
let cursor = 0, scheduled = [], path = "/strategies";
const same = (a,b) => a?.length === b?.length && a.every((v,i) => Object.is(v,b[i]));
const hooks = {
  useState(initial) { const i=cursor++; if (!(i in values)) values[i]=typeof initial==="function"?initial():initial; return [values[i], next => {values[i]=typeof next==="function"?next(values[i]):next;}]; },
  useRef(initial) {const i=cursor++; return refs[i] ||= {current:initial};},
  useCallback(fn,deps) {const i=cursor++;if (!callbacks[i] || !same(callbacks[i].deps,deps)) callbacks[i]={fn,deps};return callbacks[i].fn;},
  useEffect(fn,deps) {const i=cursor++;if (!effects[i] || !same(effects[i].deps,deps)) scheduled.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
};
globalThis.__incrementApiHooks=hooks;
globalThis.__incrementApiRestored=[];
globalThis.window={
  addEventListener(type,fn){if(!listeners.has(type)) listeners.set(type,new Set());listeners.get(type).add(fn);},
  removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
  dispatchEvent(event){for(const fn of listeners.get(event.type)||[]) fn(event);},
};
globalThis.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve,answered:false}));
const encode=value=>"data:text/javascript;base64,"+Buffer.from(value).toString("base64");
let source=ts.transpileModule(await fs.readFile(new URL("./api.ts",import.meta.url),"utf8"),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
source=source.replaceAll('"react"',JSON.stringify(encode('export const {useState,useRef,useEffect,useCallback}=globalThis.__incrementApiHooks;')))
  .replaceAll('"../components/incrementSession"',JSON.stringify(encode('export function restoreIncrementSession(value){globalThis.__incrementApiRestored.push(value);}')));
const {useApi}=await import(encode(source));
function render(){cursor=0;const result=useApi(path);const pending=scheduled;scheduled=[];pending.forEach(fn=>fn());return result;}
async function respond(status,body){const pending=requests.find(value=>!value.answered&&!value.options.signal.aborted);assert.ok(pending);pending.answered=true;pending.resolve({ok:status<400,status,text:async()=>JSON.stringify(body)});await new Promise(resolve=>setImmediate(resolve));return render();}
render();
await respond(409,{detail:{message:"increment locked",increment_session:{request_key:"one",root_task_id:"job",created_at_ms:1}}});
assert.match(render().error,/increment locked/);
assert.equal(globalThis.__incrementApiRestored.length,1);
window.dispatchEvent(new Event("workbench-increment-unlocked"));render();
assert.equal(requests.length,2,"confirmed unlock retries the lock-failed GET once");
window.dispatchEvent(new Event("workbench-increment-unlocked"));render();
assert.equal(requests.length,2,"repeated confirmation does not duplicate the GET");
await respond(200,{items:[]});assert.equal(render().error,"");
path="/topics";render();await respond(200,{items:[]});
const healthy=requests.length;
window.dispatchEvent(new Event("workbench-increment-unlocked"));render();
assert.equal(requests.length,healthy,"successful retained pages are not reloaded");
path="/sources";render();await respond(503,{detail:"offline"});
const ordinaryFailure=requests.length;
window.dispatchEvent(new Event("workbench-increment-unlocked"));render();
assert.equal(requests.length,ordinaryFailure,"unrelated failures do not turn into automatic operations");
assert.ok(requests.every(value=>!value.options.method),"unlock only rereads GET; it never replays a POST");
effects.forEach(effect=>effect?.cleanup?.());
delete globalThis.__incrementApiHooks;delete globalThis.__incrementApiRestored;delete globalThis.window;delete globalThis.fetch;
console.log("useApi increment unlock: only lock-failed GETs reread once; healthy pages and POSTs remain untouched");
