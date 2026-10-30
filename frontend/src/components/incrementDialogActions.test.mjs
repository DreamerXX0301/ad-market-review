import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe = encode(`
export * from ${JSON.stringify(import.meta.resolve("react"))};
let position=0, data=null, failure=null;export const changes=[];
export function configure(value,error=null){position=0;data=value;failure=error;changes.length=0;}
export function useState(initial){const slot=position++;const value=slot===1?data:slot===2?failure:slot===4?false:typeof initial==='function'?initial():initial;return [value,next=>changes.push([slot,next])];}
export function useEffect(){}
`);
const jsxProbe = encode(`
import {jsx as base,jsxs as bases} from ${JSON.stringify(runtime)};
export {Fragment} from ${JSON.stringify(runtime)};export const buttons=[];
function observe(type,props){if(type==='button')buttons.push(props);}
export function jsx(type,props,key){observe(type,props);return base(type,props,key);}
export function jsxs(type,props,key){observe(type,props);return bases(type,props,key);}
`);
const apiProbe = encode(`
export const requests=[];let respond=async()=>({acknowledged:true});
export function setResponse(callback){respond=callback;requests.length=0;}
export async function api(){throw Error('effects are not run by this render probe');}
export async function post(path,payload){requests.push([path,payload]);return respond(path,payload);}
`);
const sessionProbe = encode(`
let current=null;export const cleared=[];
export const INCREMENT_SESSION_EVENT='fixture-session',INCREMENT_SESSION_KEY='fixture-key';
export function setSession(value){current=value;cleared.length=0;}
export function readIncrementSession(){return current;}
export function acknowledgeIncrementSession(key){cleared.push(key);return true;}
export function restoreIncrementSession(){}
`);
const progressSource = ts.transpileModule(await fs.readFile(new URL("./incrementProgress.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const replacements = {
  react: reactProbe,
  "react/jsx-runtime": jsxProbe,
  "react-dom": encode("export function createPortal(children){return children;}"),
  "../lib/api": apiProbe,
  "../lib/format": encode("export const dateTime=String,duration=String,statusText={};"),
  "./incrementSession": sessionProbe,
  "./WorkspaceTaskSummary": encode("export default function Summary(){return null;}"),
  "./incrementProgress": encode(progressSource),
  "./NoonProgressContent": encode("export default function Noon(){return '午评采集分支';}export const useNoonRunProgress=()=>globalThis.__noonGate||({session:null,data:null,error:\"\",retry(){}});"),
  "./AuctionProgressContent": encode(`export default function Auction(){return "竞价采集分支";}export const useAuctionRunProgress=()=>globalThis.__auctionGate||({session:null,data:null,error:"",retry(){}});`),
};
let source = ts.transpileModule(await fs.readFile(new URL("./IncrementProgressDialog.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText.replace(/import\s+["'][^"']+\.css["'];?/g, "");
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)]) {
  source = source.replace(match[0], "from " + JSON.stringify(replacements[match[2]] || import.meta.resolve(match[2])));
}
const { default: Dialog } = await import(encode(source));
const { configure, changes } = await import(reactProbe);
const { buttons } = await import(jsxProbe);
const { setResponse, requests } = await import(apiProbe);
const { setSession, cleared } = await import(sessionProbe);
globalThis.document = { body: {} };
const local = { requestKey: "request-a", taskId: "job-a", epoch: "epoch-a", createdAt: 100 };
const receipt = { request_key: "request-a", root_task_id: "job-a", created_at_ms: 100,
  can_acknowledge: true, locked: true, status: "partial", elapsed_ms: 1000, tasks: [] };
function render(value, session = local, failure = null) {
  setSession(session); configure(value, failure); buttons.length = 0;
  return renderToStaticMarkup(createElement(Dialog, { onFinished() {} }));
}
function button(label) {
  const found = buttons.find(value => String(value.children).includes(label));
  assert.ok(found, label); return found;
}

let release;
setResponse(() => new Promise(resolve => { release = resolve; }));
const complete = render(receipt);
assert.match(complete, /确认本次结果后即可继续操作/);
const confirming = button("确认结果，继续使用").onClick();
assert.deepEqual(cleared, [], "clicking confirm alone must not clear the UI lock");
assert.deepEqual(requests, [["/sync/session/acknowledge", {
  request_key: "request-a", task_id: "job-a", session_epoch: "epoch-a",
}]]);
release({ acknowledged: true }); await confirming;
assert.deepEqual(cleared, ["request-a"], "only an acknowledged server result unlocks");

setResponse(async () => { throw Error("offline"); });
render(receipt); await button("确认结果，继续使用").onClick();
assert.deepEqual(cleared, [], "network failure must retain the lock");
assert.ok(changes.some(([slot, value]) => slot === 2 && value.error === "offline"));

setResponse(async () => ({ stopping: true }));
const active = { ...receipt, can_acknowledge: false, status: "running", tasks: [
  { job_id: "sector", title: "板块K线尾部增量", status: "running", progress: 42 },
] };
const running = render(active);
assert.match(running, /42%/);
assert.ok(!buttons.some(value => String(value.children).includes("确认结果，继续使用")));
assert.ok(!buttons.some(value => /暂停|取消/.test(String(value.children))), "running collection has no stop action");
assert.deepEqual(requests, []);
assert.deepEqual(cleared, []);
render({ ...active, status: "stopping" });
assert.ok(!buttons.some(value => /暂停|取消/.test(String(value.children))));

setResponse(async () => ({ acknowledged: true, status: "not_received" }));
render(null, { requestKey: "unreceived", createdAt: 100 }, { identifier: "unreceived", missing: true, error: "404" });
await button("核查并关闭未接收请求").onClick();
assert.deepEqual(requests, [["/sync/session/acknowledge", {
  request_key: "unreceived", task_id: undefined, session_epoch: undefined,
}]]);
assert.deepEqual(cleared, ["unreceived"]);
assert.ok(requests.every(([path]) => !/sync\/tasks|resume|retry/.test(path)));
globalThis.__noonGate={session:{date:"2026-10-28",requestKey:"noon"},data:null,error:"",retry(){}};
const noonOnly=render(null,null);
assert.match(noonOnly,/午评采集分支/);
assert.equal((noonOnly.match(/increment-progress-backdrop/g)||[]).length,1,"noon reuses the sole lock portal");
const both=render(active);
assert.ok(!both.includes("午评采集分支"),"increment owns the content when both tasks exist");
assert.match(both,/正在更新盘后数据/);
delete globalThis.__noonGate;
globalThis.__auctionGate={session:{date:"2026-10-29",requestKey:"auction"},data:{status:"waiting_retry"},error:"",retry(){}};
const auctionOnly=render(null,null);
assert.match(auctionOnly,/竞价采集分支/);
assert.equal((auctionOnly.match(/increment-progress-backdrop/g)||[]).length,1,"auction reuses the same single modal portal");
assert.doesNotMatch(render(active),/竞价采集分支/,"increment retains priority over auction");
delete globalThis.__auctionGate;
globalThis.__auctionGate={session:null,checking:true,data:null,error:"",retry(){}};
assert.equal(render(null,null),"","unconfirmed auction discovery never adds an empty initial lock");
delete globalThis.__auctionGate;
delete globalThis.document;
console.log("Increment dialog actions: server acknowledgement, failed confirmation, safe controls and unreceived request closure passed");
