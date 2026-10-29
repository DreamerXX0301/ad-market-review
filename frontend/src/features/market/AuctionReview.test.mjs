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
const apiProbe = encode(`let packet=null,close=null,automatic=null,topics=null,feedback=null,resonance=null;export const requests=[];export function setData(value,review,settings=null,topicView=null,feedbackView=null,resonanceView=null){packet=value;close=review;automatic=settings;topics=topicView;feedback=feedbackView;resonance=resonanceView;requests.length=0;}
export const usePageActive=()=>true;export function useApi(path){requests.push(path);return{data:path?.startsWith('/market/auction?')?packet:path?.startsWith('/market/auction/close-review?')?close:path==='/market/auction/automation'?automatic:path?.startsWith('/market/auction/topic-summary?')?topics:path?.startsWith('/market/auction/feedback?')?feedback:path?.startsWith('/market/auction/resonance?')?resonance:null,loading:false,error:'',reload(){}};}
export function api(){throw Error('Rendering never reads outside useApi');}export function post(){throw Error('Page rendering must not submit a source task');}export const requestKey=()=> 'fixture-request';`);
const uiProbe = encode(`import{jsx,jsxs}from ${JSON.stringify(runtime)};export const useToast=()=>()=>{};
export const Modal=({children})=>jsx('div',{children});export const Badge=({children})=>jsx('span',{children});export const Panel=({title,subtitle,children,actions})=>jsxs('section',{children:[jsx('h2',{children:title}),jsx('p',{children:subtitle}),children,actions]});
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
function render(packet,states={},close=null,automatic=null,observedDate=day,topics=null,feedback=null,resonance=null){setData(packet,close,automatic,topics,feedback,resonance);setStates(states);return renderToStaticMarkup(createElement(AuctionReview,{date:observedDate,onDate(){},onStock(){},chartDate:'2026-10-28'})).replaceAll(/<!--[^]*?-->/g,'');}
let html=render(view);
assert.match(html,/先留下一份真实的竞价观察/);
assert.doesNotMatch(html,/样本竞价涨幅中位数|昨日我的原判断/,'empty view cannot invent any snapshot statistics or prior text');
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",null,null],'collapsed posthoc review is not read before a snapshot exists');
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
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",null,`/market/auction/topic-summary?date=${day}`,`/market/auction/feedback?date=${day}`,`/market/auction/resonance?date=${day}`]);
html=render({...retained,date:'2026-10-28'});
assert.doesNotMatch(html,/昨日我的原判断|98\.00万元/,'date switch must not leak prior response');
const benchmarkSample={...retained,snapshot:{...retained.snapshot,payload:{...payload,benchmark:{state:'partial',items:[
  {instrument_id:'600003.SH',name:'基准低开样本',auction_pct:-3,tags:['来源低开']},
  {instrument_id:'600004.SH',name:'基准高开样本',auction_pct:5,tags:['来源高开']},
  {instrument_id:'600005.SH',name:'基准平开样本',auction_pct:0,tags:[]},
  {instrument_id:'600006.SH',name:'基准未知样本',auction_pct:null,tags:[]},
]}}}};
html=render(benchmarkSample);
assert.match(html,/来源短线基准涨幅排序/);
assert.ok(html.indexOf('基准高开样本')<html.indexOf('基准平开样本'));
assert.ok(html.indexOf('基准平开样本')<html.indexOf('基准低开样本'));
assert.ok(html.indexOf('基准低开样本')<html.indexOf('基准未知样本'));
assert.match(html,/来源高开/,'sorting preserves source labels');
html=render(benchmarkSample,{5:'asc'});
assert.ok(html.indexOf('基准低开样本')<html.indexOf('基准平开样本'));
assert.ok(html.indexOf('基准平开样本')<html.indexOf('基准高开样本'));
assert.ok(html.indexOf('基准高开样本')<html.indexOf('基准未知样本'),'unknown stays last in both directions');
html=render(retained,{0:true},{date:day,state:'pending',reason:'等待同日盘后增量发布',posthoc:true});
assert.match(html,/等待同日盘后增量发布/);
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",`/market/auction/close-review?date=${day}`,`/market/auction/topic-summary?date=${day}`,`/market/auction/feedback?date=${day}`,`/market/auction/resonance?date=${day}`]);
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
const topics={version:'auction-topic-summary-v1',date:day,state:'observed',coverage:{requested:2,classified:2,unclassified:0},groups:[{topic_code:'886001.TI',name:'合成题材甲',kind:'concept',source:'fuyao',members_observed_date:'2026-10-21',sample_count:2,valid_count:2,missing_count:0,average_auction_pct:1.5,instrument_ids:['600001.SH','000001.SZ']}]};
html=render(retained,{},null,null,day,topics);
assert.match(html,/题材数量与均幅/);
assert.match(html,/合成题材甲/);
assert.match(html,/竞价均幅/);
assert.match(html,/\+1\.50%/);
assert.match(html,/成员档案 2026-10-21/,'membership and auction dates stay separate');
assert.match(html,/竞价 2026-10-29/);
html=render(retained,{},null,null,day,{...topics,date:'2026-10-28'});
assert.doesNotMatch(html,/合成题材甲/,'stale derived topic response cannot cross dates');
const feedback={version:'auction-feedback-v1',date:day,previous_date:'2026-10-28',coverage:{requested:2,classified:1,unknown:1},groups:[{key:'repair',title:'昨日炸板→今日高开',interpretation:'等待开盘后核对',count:1,valid_count:1,missing_count:0,average_auction_pct:2,median_auction_pct:2,items:[{instrument_id:'600001.SH',name:'合成修复甲',auction_pct:2}]},{key:'unknown_quote',title:'竞价缺失',count:1,valid_count:0,missing_count:1,average_auction_pct:null,items:[]}]};
html=render(retained,{},null,null,day,null,feedback);
assert.match(html,/修复观察/);
assert.match(html,/昨日炸板→今日高开/);
assert.match(html,/合成修复甲/);
assert.match(html,/\+2\.00%/);
assert.match(html,/未分组 1只/);
const tagged={...retained,snapshot:{...retained.snapshot,payload:{...payload,universe:{...payload.universe,items:[{instrument_id:'600001.SH',reasons:['严格前日已核验涨停'],previous_board_count:3},{instrument_id:'000001.SZ',reasons:['严格前日已核验炸板']}]}}}};
html=render(tagged);
assert.match(html,/class="auction-background-tag limit-up" title="严格前日已核验涨停">昨日涨停/);
assert.match(html,/class="auction-background-tag break" title="严格前日已核验炸板">昨日炸板/);
assert.match(html,/昨日3板/);
console.log('Actual auction page: honest empty/preparation states, original judgment versus source text, missing/zero, lazy posthoc read and date isolation passed');

const resonance={version:'auction-resonance-v1',date:day,strong_sample:{count:20,valid_count:100},groups:[{topic_code:'886001.TI',name:'合成共振题材',kind:'concept',source:'fuyao',members_observed_date:'2026-10-21',sample_count:5,valid_count:5,top_count:3,concentration_lift:3,up_count:4,up_ratio:.8,average_auction_pct:2,median_auction_pct:1.5,top_instrument_ids:['600001.SH']}],anchors:{state:'unknown'}};
html=render(retained,{},null,null,day,null,null,resonance);
assert.match(html,/合成共振题材/);
assert.match(html,/3.00倍/);
assert.match(html,/80%/);
assert.match(html,/今日快照缺少涨停价与一字证据/);
html=render(retained,{},null,null,day,null,null,{...resonance,date:'2026-10-28'});
assert.doesNotMatch(html,/合成共振题材/,'resonance response cannot cross dates');

const classifiedTopics={...topics,stock_classifications:{'600001.SH':{industries:[{topic_code:'881001.TI',source:'fuyao',name:'合成核验行业',members_observed_date:'2026-10-21'}],topics:[{topic_code:'886001.TI',source:'fuyao',name:'合成核验概念',members_observed_date:'2026-10-21'}]}}};
html=render(retained,{},null,null,day,classifiedTopics,feedback);
assert.match(html,/行业 \/ 概念/);
assert.match(html,/合成核验行业/);
assert.match(html,/合成核验概念/);
assert.match(html,/复制行业代码 881001.TI/);
assert.match(html,/复制概念代码 886001.TI/);
assert.match(html,/复制六位代码 600001/);
html=render(retained,{},null,null,day,{...classifiedTopics,date:'2026-10-28'},feedback);
assert.doesNotMatch(html,/合成核验行业|合成核验概念/,'classification tags cannot leak across dates');
const {default:AuctionStockTiles}=await import(await moduleUrl(new URL('./AuctionStockTiles.tsx',import.meta.url)));
setStates({});
html=renderToStaticMarkup(createElement(AuctionStockTiles,{ids:['600003.SH','600004.SH','600005.SH'],quotes:[{instrument_id:'600003.SH',name:'低开卡片',state:'observed',auction_pct:-2},{instrument_id:'600004.SH',name:'高开卡片',state:'observed',auction_pct:5},{instrument_id:'600005.SH',name:'未知卡片',state:'unknown',auction_pct:99}]}));
assert.ok(html.indexOf('高开卡片')<html.indexOf('低开卡片'));
assert.ok(html.indexOf('低开卡片')<html.indexOf('未知卡片'));
assert.match(html,/\+5\.00%/);assert.match(html,/-2\.00%/);assert.doesNotMatch(html,/99\.00%/);
html=render(retained,{},null,null,day,classifiedTopics,feedback);
assert.match(html,/主要关联行业概念均幅/);assert.match(html,/每页20只/);assert.doesNotMatch(html,/观察单分组方式/);

const {default:AuctionResonance}=await import(await moduleUrl(new URL('./AuctionResonance.tsx',import.meta.url)));
const sortGroups=[2,0,null].map((value,index)=>({...resonance.groups[0],topic_code:`88600${index+1}.TI`,name:['排序高值','排序零值','排序未知'][index],top_count:2,concentration_lift:value,average_auction_pct:value,median_auction_pct:value,up_ratio:value===null?null:value/10,instrument_ids:[]}));
for(const key of ['concentration_lift','average_auction_pct','median_auction_pct','up_ratio'])for(const direction of ['desc','asc']){
 setData(retained,null,null,null,null,{...resonance,groups:sortGroups});setStates({2:{key,direction}});
 const table=renderToStaticMarkup(createElement(AuctionResonance,{date:day,quotes:[]}));
 assert.ok(table.indexOf(direction==='desc'?'排序高值':'排序零值')<table.indexOf(direction==='desc'?'排序零值':'排序高值'));
 assert.ok(table.indexOf('排序未知')>table.indexOf('排序高值'));assert.ok(table.indexOf('排序未知')>table.indexOf('排序零值'));
 assert.match(table,new RegExp('aria-sort="'+(direction==='desc'?'descending':'ascending')+'"'));
}
const selectableTopics={...classifiedTopics,groups:[{...topics.groups[0],sample_count:3,valid_count:3,instrument_ids:['600001.SH'],average_auction_pct:-2}]};
html=render(retained,{7:{date:day,key:'fuyao:concept:886001.TI'}},null,null,day,selectableTopics,feedback);
const selectedTable=html.slice(html.indexOf('<table class="auction-table"'),html.indexOf('</table>',html.indexOf('<table class="auction-table"')));
assert.match(selectedTable,/600001/);assert.doesNotMatch(selectedTable,/000001/);assert.match(html,/取消选中/);assert.match(html,/<details open=""/);assert.match(html,/data-associated="true"/);
assert.equal((html.match(/aria-pressed="true" aria-label="合成题材甲/g)||[]).length,2,'both association strips share selection');
assert.ok((html.match(/<div class="auction-association-summary"[^]*?<\/div>/g)||[]).every(strip=>!strip.includes('复制概念代码')),'summary chip is selection, not copy');
html=render(retained,{7:{date:'2026-10-28',key:'fuyao:concept:886001.TI'}},null,null,day,selectableTopics,feedback);assert.doesNotMatch(html,/取消选中/,'selection must be date-bound');
for(const direction of ['asc','desc']){
 setData(retained,null,null,null,null,{...resonance,groups:sortGroups.slice(0,2).map((row,index)=>({...row,name:index?'ZuluGroup':'AlphaGroup',top_count:index?4:2}))});
 for(const key of ['name','top_count']){
  setStates({2:{key,direction}});const result=renderToStaticMarkup(createElement(AuctionResonance,{date:day,quotes:[]}));
  assert.ok(result.indexOf(direction==='asc'?'AlphaGroup':'ZuluGroup')<result.indexOf(direction==='asc'?'ZuluGroup':'AlphaGroup'));
 }
}
html=render(retained,{},null,null,day,classifiedTopics,feedback,resonance);
assert.match(html,/aria-label="盘前竞价页内目录"/);
for(const id of ['auction-overview','auction-automation','auction-feedback','auction-previous','auction-low-open','auction-resonance','auction-topics','auction-observation','auction-benchmark','auction-close-review','auction-evidence']){
 assert.match(html,new RegExp('aria-controls="'+id+'"'));assert.match(html,new RegExp('id="'+id+'"'));
}
html=render({...view,state:'purged',locked_for_day:true,retention:{notice:'保留28个自然日，到期随成功盘后增量清理。'}});
assert.match(html,/竞价已按保留规则清理/);assert.match(html,/只保留日任务台账和清理回执/);assert.match(html,/保留28个自然日/);assert.doesNotMatch(html,/先留下一份真实的竞价观察/);

const {default:AuctionFeedback}=await import(await moduleUrl(new URL('./AuctionFeedback.tsx',import.meta.url)));
const unknownLimit={key:'limit_up_open',title:'今日涨停价开盘',state:'unknown',count:null,valid_count:0,missing_count:0,average_auction_pct:null,median_auction_pct:null,items:[],reason:'原竞价来源未提供今日涨停价及涨停开盘标记，暂不能核验。'};
function renderFeedback(groups,ids=null){
 setData(retained,null,null,null,{...feedback,groups});setStates({});
 return renderToStaticMarkup(createElement(AuctionFeedback,{date:day,chartDate:'2026-10-28',onStock(){},topicView:classifiedTopics,selectedIds:ids,selectedKey:ids?'selected-topic':undefined})).replaceAll(/<!--[^]*?-->/g,'');
}
function limitCard(markup){return markup.match(/<article[^>]*data-feedback-group="limit_up_open"[^>]*>[^]*?<\/article>/)?.[0]||'';}
html=renderFeedback([feedback.groups[0],unknownLimit]);
assert.ok(html.indexOf('data-feedback-group="limit_up_open"')<html.indexOf('data-feedback-group="repair"'),'limit-open card stays first even when source groups are reordered');
let anchor=limitCard(html);assert.match(anchor,/一字涨停开/);assert.match(anchor,/待核验/);assert.match(anchor,/竞价均幅[^]*?—/);
assert.match(anchor,/原竞价来源未提供今日涨停价/);assert.doesNotMatch(anchor,/复制|查看本组股票|<details|0只/,'unknown evidence must not expose empty actions or zero counts');
html=renderFeedback([unknownLimit,feedback.groups[0]],new Set(['600001.SH']));
anchor=limitCard(html);assert.match(anchor,/待核验/);assert.doesNotMatch(anchor,/0只/);assert.match(html,/已分组 1只/);assert.doesNotMatch(html,/NaN/);
assert.match(html,/<details open=""/);assert.match(html,/data-associated="true"/,'selection still expands and highlights the other groups');
html=renderFeedback([feedback.groups[0]]);
assert.match(limitCard(html),/待核验/,'older compatible API responses keep an honest first card instead of hiding the category');
const referenceLimit={...unknownLimit,state:'reference',title:'涨停价高开参考',count:2,valid_count:2,missing_count:0,average_auction_pct:10.01,median_auction_pct:10.01,items:[{instrument_id:'600007.SH',name:'合成涨停开参考甲',auction_pct:10},{instrument_id:'600008.SH',name:'合成涨停开参考乙',auction_pct:10.02}]};
html=renderFeedback([feedback.groups[0],referenceLimit]);anchor=limitCard(html);
assert.match(anchor,/一字涨停开/);assert.match(anchor,/class="auction-feedback-reference">参考/);assert.match(anchor,/竞价涨停开参考/);
assert.match(anchor,/常规档参考，不确认当日限价或全天一字/);assert.match(anchor,/2只/);assert.match(anchor,/\+10\.01%/);
assert.match(anchor,/合成涨停开参考甲/);assert.match(anchor,/复制一字涨停开股票/);assert.match(anchor,/查看本组股票/);assert.doesNotMatch(anchor,/class="auction-feedback-pending"/);
html=renderFeedback([referenceLimit,feedback.groups[0]],new Set(['600008.SH']));anchor=limitCard(html);
assert.match(anchor,/1只/);assert.match(anchor,/\+10\.02%/);assert.match(anchor,/合成涨停开参考乙/);assert.doesNotMatch(anchor,/合成涨停开参考甲/);
assert.match(anchor,/<details open=""/);assert.match(anchor,/data-associated="true"/);
html=renderFeedback([{...referenceLimit,state:'unknown',count:77,average_auction_pct:777}]);anchor=limitCard(html);
assert.match(anchor,/待核验/);assert.doesNotMatch(anchor,/77只|777|合成涨停开参考甲|复制|<details/,'unknown state wins over unverified numeric or stock fields');
console.log('Auction limit-open card: first position, visible reference boundary, real subset statistics, unknown preservation and existing selection highlights passed');
