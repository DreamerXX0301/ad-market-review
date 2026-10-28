import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { build } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Render the actual page and invoke its actual submit/resume handlers. Only
// local HTTP responses and JSX observation are replaced; no business DB/API runs.
const root = new URL("../../../", import.meta.url).pathname;
const output = await build({
  configFile: false, root, logLevel: "silent",
  plugins: [{
    name: "noon-submission-receipts", enforce: "pre",
    resolveId(source, importer) {
      if (source.includes("lib/api") && importer?.endsWith("/NoonReview.tsx")) return "\0fixture-noon-api";
    },
    load(id) {
      if (id === "\0fixture-noon-api") return `
        const state=()=>globalThis.__noonSubmission;
        export const useApi=()=>({data:state().view,loading:false,error:null,reload(){state().reloads++;}});
        export const usePageActive=()=>true;
        export const requestKey=()=>state().key;
        export const post=(...args)=>{state().posts.push(args);return state().post(...args);};
        export const api=(...args)=>{state().gets.push(args);return state().get(...args);};`;
    },
    transform(code,id) {
      if(id.endsWith("/NoonReview.tsx")) return code+`\nexport {readNoonRun,releaseNoonRun,reconcileNoonRun} from ${JSON.stringify(path.join(root,"src/components/noonRunLock.ts"))};`;
    },
  }],
  build: { write:false, ssr:new URL("./NoonReview.tsx",import.meta.url).pathname, minify:false },
});
const encode=value=>"data:text/javascript;base64,"+Buffer.from(value).toString("base64");
const require=createRequire(import.meta.url);
const jsxProbe=encode(`import * as real from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))};
  export const Fragment=real.Fragment;
  export function jsx(type,props,key){if(type==='button')globalThis.__noonButtons.push(props);return real.jsx(type,props,key);}
  export function jsxs(type,props,key){if(type==='button')globalThis.__noonButtons.push(props);return real.jsxs(type,props,key);}`);
const chunk=output.output.find(item=>item.type==="chunk"&&item.isEntry);
const code=chunk.code.replace(/from "([^".][^"]*)"/g,(_,id)=>`from ${JSON.stringify(id==="react/jsx-runtime"?jsxProbe:pathToFileURL(require.resolve(id)).href)}`);
const directory=await fs.mkdtemp(path.join(tmpdir(),"noon-submission-race-"));
const modulePath=path.join(directory,"component.mjs");
await fs.writeFile(modulePath,code);
const store=new Map(),events=[];
globalThis.localStorage={getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value),removeItem:key=>store.delete(key)};
globalThis.window={dispatchEvent:event=>events.push(event),setTimeout,clearTimeout};
const {default:NoonReview,readNoonRun,releaseNoonRun,reconcileNoonRun}=await import(pathToFileURL(modulePath).href);
const day="2026-10-28";
const task=(status,key="receipt",logs=[])=>({task_id:"noon-original",kind:"source_check",parameters:{operation:"noon_snapshot"},target_date:day,request_key:key,created_at_ms:1,status,logs});
const view=(state="not_collected",saved=null)=>({date:day,today:day,state,task:saved,locked_for_day:!!saved,can_capture:!saved,capture_window:{allowed:true}});
function configure(saved,key){
  globalThis.__noonSubmission={view:saved,key,posts:[],gets:[],reloads:0,get:async()=>view(),post:async()=>{throw Error("unconfigured POST");}};
  globalThis.__noonButtons=[];
  renderToStaticMarkup(createElement(NoonReview,{date:day,onDate(){},onStock(){}}));
  return globalThis.__noonSubmission;
}
const button=name=>{const value=globalThis.__noonButtons.find(item=>Array.isArray(item.children)?item.children.includes(name):item.children===name);assert.ok(value,`actual page action: ${name}`);assert.equal(value.disabled,false);return value;};
const flush=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
try {
  let state=configure(view(),"lost-create");
  let rejectPost;
  state.post=()=>new Promise((_,reject)=>rejectPost=reject);
  const pending=button("手动采集午评").onClick();
  assert.equal(readNoonRun().requestKey,"lost-create","lock precedes the actual POST response");
  rejectPost(new TypeError("connection lost"));await pending;
  assert.equal(state.posts.length,1);assert.equal(state.gets.length,1);
  assert.equal(readNoonRun().requestKey,"lost-create","lost POST plus transient not_collected must remain globally blocked");
  assert.equal(reconcileNoonRun("lost-create",{...view("queued",task("queued","lost-create"))}),true);
  assert.equal(readNoonRun().taskId,"noon-original");
  assert.equal(reconcileNoonRun("lost-create",view("failed",task("failed","lost-create"))),true);
  assert.equal(readNoonRun(),null,"a later accepted matching terminal receipt releases");

  state=configure(view(),"server-unknown");
  state.post=async()=>{throw Object.assign(Error("response unavailable"),{status:503});};
  await button("手动采集午评").onClick();assert.ok(readNoonRun());releaseNoonRun("server-unknown");
  for(const status of [409,422]) {
    state=configure(view(),`rejected-${status}`);
    state.post=async()=>{throw Object.assign(Error("request rejected"),{status});};
    state.get=async()=>{throw Error("follow-up offline");};
    await button("手动采集午评").onClick();
    assert.equal(readNoonRun(),null,"an explicit admission rejection cannot leave a permanent gate");
  }
  state=configure(view(),"other-tab-conflict");
  state.post=async()=>{throw Object.assign(Error("day already admitted"),{status:409});};
  state.get=async()=>view("running",task("running","other-tab"));
  const eventStart=events.length;
  await button("手动采集午评").onClick();
  assert.equal(readNoonRun().requestKey,"other-tab");
  assert.ok(events.slice(eventStart).every(event=>event.detail!==null),"409 hands the gate to the existing run without an unlocked event");
  releaseNoonRun("other-tab");

  const original=task("paused","original-request",[{seq:21,stage:"queued"},{seq:22,stage:"paused"}]);
  state=configure(view("paused",original),"lost-resume");
  state.post=async()=>{throw new TypeError("resume response lost");};
  state.get=async()=>view("paused",original);
  await button("继续原任务").onClick();
  assert.equal(state.posts[0][0],"/tasks/noon-original/resume");
  assert.equal(readNoonRun().resumeTaskId,"noon-original");
  assert.equal(readNoonRun().resumeAfterSeq,22);
  assert.equal(readNoonRun().taskId,undefined,"the original paused receipt does not prove resume admission");
  for(const status of ["paused","failed","cancelled"])
    assert.equal(reconcileNoonRun("lost-resume",view(status,{...original,status})),false);
  const resumed={...original,status:"paused",logs:[...original.logs,{seq:23,stage:"queued"},{seq:24,stage:"paused"}]};
  assert.equal(reconcileNoonRun("lost-resume",view("paused",resumed)),true);
  assert.equal(readNoonRun(),null,"new queued log proves a completed resume even when its active interval was missed");

  state=configure(view("paused",original),"resume-refused");
  state.post=async()=>{throw Object.assign(Error("window ended"),{status:409});};
  state.get=async()=>view("paused",original);
  await button("继续原任务").onClick();assert.equal(readNoonRun(),null);
  state=configure(view("paused",original),"resume-confirmed");
  state.post=async()=>original;
  await button("继续原任务").onClick();assert.equal(readNoonRun(),null,"a completed HTTP response can confirm a no-op terminal resume");
  assert.equal(state.gets.length,0);
  await flush();
} finally {
  await fs.rm(directory,{recursive:true,force:true});
  delete globalThis.__noonSubmission;delete globalThis.__noonButtons;
}
console.log("Actual NoonReview actions: lost capture/resume responses, transient missing/old terminal states, new resume generation, safe rejection and atomic 409 handoff passed");
