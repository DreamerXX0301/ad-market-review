/** Real page SSR uses only synthetic local-read packets; it never creates jobs. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (value) => "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const react = import.meta.resolve("react"), runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe = encode(`import * as React from ${JSON.stringify(react)}; export * from ${JSON.stringify(react)};
let cursor=0,states={};export function setStates(value){cursor=0;states=value;}
export function useState(initial){const index=cursor++;return React.useState(Object.hasOwn(states,index)?states[index]:initial);}`);
const apiProbe = encode(`let packet=null,close=null,automatic=null;export const requests=[];export function setData(value,review,settings=null){packet=value;close=review;automatic=settings;requests.length=0;}
export const usePageActive=()=>true;export function useApi(path){requests.push(path);return{data:path?.startsWith('/market/auction?')?packet:path?.startsWith('/market/auction/close-review?')?close:path==='/market/auction/automation'?automatic:null,loading:false,error:'',reload(){}};}
export function api(){throw Error('Rendering never reads outside useApi');}export function post(){throw Error('Page rendering must not submit a source task');}export const requestKey=()=> 'fixture-request';`);
const uiProbe = encode(`import{jsx,jsxs}from ${JSON.stringify(runtime)};export const useToast=()=>()=>{};
export const Badge=({children})=>jsx('span',{children});export const Panel=({title,subtitle,children,actions})=>jsxs('section',{children:[jsx('h2',{children:title}),jsx('p',{children:subtitle}),children,actions]});
export const Empty=({title,description})=>jsxs('div',{className:'empty-state',children:[jsx('h3',{children:title}),jsx('p',{children:description})]});export const ErrorBox=({message})=>jsx('p',{role:'alert',children:message});export const Loading=()=>jsx('p',{children:'读取中'});`);
const replacements = { react: reactProbe, "../../lib/api": apiProbe, "../../components/UI": uiProbe };
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let source = ts.transpileModule(await fs.readFile(url,"utf8"),{fileName:url.pathname,compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
    source=source.replace(/^import ["'][^"']+\.css["'];?$/gm,"");
    for(const name of [...new Set([...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match=>match[1]))]){
      let target=replacements[name];
      if(!target&&name.startsWith('.')){for(const extension of ['.ts','.tsx']){const candidate=new URL(name+extension,url);try{await fs.access(candidate);target=await moduleUrl(candidate);break;}catch(error){if(error.code!=='ENOENT')throw error;}}if(!target)throw Error('Missing '+name);}
      target ||= import.meta.resolve(name);
      source=source.replaceAll('from "'+name+'"','from '+JSON.stringify(target)).replaceAll("from '"+name+"'",'from '+JSON.stringify(target));
    }
    return encode(source);
  })();cache.set(url.href,job);return job;
}
const { default: AuctionReview } = await import(await moduleUrl(new URL('./AuctionReview.tsx',import.meta.url)));
const { setData, requests } = await import(apiProbe), { setStates } = await import(reactProbe);
const day='2026-10-29';
const original={date:'2026-10-28',state:'retained',recorded_at_ms:Date.parse('2026-10-28T20:00:00+08:00'),summary:['原留存氛围'],payload:{atmosphere:{summary:'原留存氛围'}},observations:[{instrument_id:'600001.SH',name:'合成观察甲',mainline:'昨日原线',latest_price:9999,reason:'午间临时缺报不应显示'}],mainlines:[{date:'2026-10-28',instrument_id:'600001.SH',display_mainline:'昨日原线',recorded_at_ms:Date.parse('2026-10-28T19:00:00+08:00'),attributions:[{user_note:'昨日我的原判断',source_reason:'昨日来源转述',source_name:'合成来源'}]}]};
const view={date:day,today:day,now:day+'T09:00:00+08:00',state:'not_collected',can_capture:false,capture_window:{allowed:false,reason:'请在09:25后手动采集'}};
function render(packet,states={},close=null,automatic=null,observedDate=day){setData(packet,close,automatic);setStates(states);return renderToStaticMarkup(createElement(AuctionReview,{date:observedDate,onDate(){},onStock(){},chartDate:'2026-10-28'})).replaceAll(/<!--[^]*?-->/g,'');}
let html=render(view);
assert.match(html,/先留下一份真实的竞价观察/);
assert.doesNotMatch(html,/样本竞价涨幅中位数|昨日我的原判断/,'empty view cannot invent any snapshot statistics or prior text');
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",null],'collapsed posthoc review is not read before a snapshot exists');
html=render({...view,preparation:{...original,frozen:false,relation:'last_published_day_pending_calendar_check',notice:'最近已发布日材料，严格前日待核验'}});
assert.match(html,/采集前准备 · 最近已发布日/);
assert.match(html,/尚未冻结/);
assert.match(html,/昨日我的原判断/);
assert.match(html,/竞价尚未采集/);
assert.match(html,/严格前日待核验/);
assert.doesNotMatch(html,/9,999|午间临时缺报不应显示/);
const payload={date:day,version:'auction-snapshot-v1',previous_date:'2026-10-28',collected_start_at:day+'T09:25:10+08:00',universe:{requested:2,omitted:0,items:[{instrument_id:'600001.SH',reasons:['昨日原关注']},{instrument_id:'000001.SZ',reasons:['自选']}]},quotes:{auction_phase:'final',data_status:'ready',summary:{requested:2,valid:1,missing:1,up:0,down:1,flat:0,median_pct:-2,amount:980000,amount_valid:1},items:[{instrument_id:'600001.SH',name:'合成观察甲',state:'observed',auction_price:9.8,auction_pct:-2,auction_amount:980000,auction_volume:1000,auction_turnover_pct:0,auction_volume_ratio:1.2,auction_yesterday_ratio_pct:3},{instrument_id:'000001.SZ',name:'合成缺失乙',state:'unknown',reason:'竞价缺失保持未知',auction_price:null,auction_pct:null}]},previous_review:original,benchmark:{state:'unknown',items:[],reason:'同日基准缺失'},analysis:['真实冻结样本观察说明'],gaps:[],sources:[]};
const retained={...view,state:'available',snapshot:{date:day,quality:'partial',payload,recorded_at_ms:Date.parse(day+'T09:25:20+08:00'),collected_start_at:payload.collected_start_at,collected_end_at:day+'T09:25:20+08:00'}};
html=render(retained);
assert.match(html,/昨日复盘 → 今日竞价/);
assert.match(html,/昨日我的原判断/);
assert.match(html,/我的判断/);
assert.match(html,/来源转述/);
assert.match(html,/开盘后核对/);
assert.match(html,/-2\.00%/);
assert.match(html,/98\.00万元/);
assert.match(html,/同日基准缺失/);
assert.match(html,/竞价缺失保持未知/);
assert.doesNotMatch(html,/9,999|午间临时缺报不应显示/);
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",null]);
html=render({...retained,date:'2026-10-28'});
assert.doesNotMatch(html,/昨日我的原判断|98\.00万元/,'date switch must not leak prior response');
html=render(retained,{0:true},{date:day,state:'pending',reason:'等待同日盘后增量发布',posthoc:true});
assert.match(html,/等待同日盘后增量发布/);
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",`/market/auction/close-review?date=${day}`]);
html=render(retained,{0:true},{date:day,state:'partial',posthoc:true,summary:{requested:2,valid:1,missing:1,above_auction:1,below_auction:0,flat:0,median_auction_to_close_pct:2,open_mismatch:1},items:[{instrument_id:'600001.SH',name:'合成观察甲',state:'mismatch',auction_price:9.8,auction_pct:-2,open:10,close:11,auction_to_close_pct:987.65,reason:'开盘价不一致'}]});
assert.match(html,/开盘价不一致/);
assert.doesNotMatch(html,/987\.65/,'mismatched opening reference must not display a comparable return');
html=render(retained,{0:true},{date:day,state:'unknown',posthoc:true,reason:'本次全部开盘价与竞价价不一致，暂无可比样本',summary:{requested:1,valid:0,missing:0,above_auction:null,below_auction:null,flat:null,median_auction_to_close_pct:null,open_mismatch:1},items:[{instrument_id:'600001.SH',name:'合成全差异甲',state:'mismatch',auction_price:10,auction_pct:0,open:10.5,close:11,auction_to_close_pct:999.99,reason:'开盘价不一致，保留实际同日价格'}]});
assert.match(html,/本次全部开盘价与竞价价不一致/);
assert.match(html,/开盘价不一致 1只/,'all-mismatch coverage must remain visible even when overall state is unknown');
assert.match(html,/<th>开盘（元）<\/th>/,'all-mismatch facts must not disappear with the comparable-sample state');
assert.match(html,/<td>10\.50<\/td>/);
assert.match(html,/<td>11\.00<\/td>/);
assert.match(html,/合成全差异甲/);
assert.doesNotMatch(html,/999\.99/,'an all-mismatch report still never displays a comparable price difference');
const automatic={version:'auction-automation-v1',revision:1,mode:'daily',date:null,instrument_ids:[],today:day,now:day+'T09:00:00+08:00',timezone:'Asia/Shanghai',start_time:'09:25:05',end_time:'09:30:00',enabled:true,next_run_at:day+'T09:25:05+08:00',can_enable:true,reason:null,last_run:null,worker:{alive:true,last_heartbeat_at_ms:Date.parse(day+'T09:00:00+08:00')}};
html=render(view,{},null,automatic);
assert.match(html,/服务自动采集：已开启/);
assert.match(html,/每个交易日，由项目服务/);
assert.match(html,/等待服务自动采集/);
assert.match(html,/已安排今天的自动竞价/);
assert.match(html,/浏览器可以关闭/);
assert.doesNotMatch(html,/在交易日09:25后手动采集。没有留存/);
assert.match(html,/工作进程在线/);
html=render({...view,date:'2026-10-28'}, {},null,automatic,'2026-10-28');
assert.match(html,/服务自动采集：已开启/,'global schedule remains today-scoped on historical view');
assert.doesNotMatch(html,/已安排今天的自动竞价/,'historical snapshot must not be labeled as today scheduled run');
console.log('Actual auction page: honest empty/preparation states, original judgment versus source text, missing/zero, lazy posthoc read and date isolation passed');
