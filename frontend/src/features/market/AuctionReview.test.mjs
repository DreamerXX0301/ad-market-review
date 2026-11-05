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
export function useState(initial){const index=cursor++;return React.useState(Object.hasOwn(states,index)?states[index]:initial==="focus"&&states.auctionScope?states.auctionScope:initial?.key==="auction_pct"&&states.discoverySort?states.discoverySort:initial);}`);
const apiProbe = encode(`let packet=null,close=null,automatic=null,topics=null,feedback=null,resonance=null,discovery=null,discoveryHistory=null;export const requests=[];export function setData(value,review,settings=null,topicView=null,feedbackView=null,resonanceView=null,discoveryView=null,historyView=null){packet=value;close=review;automatic=settings;topics=topicView;feedback=feedbackView;resonance=resonanceView;discovery=discoveryView;discoveryHistory=historyView;requests.length=0;}
export const usePageActive=()=>true;export function useApi(path){requests.push(path);return{data:path?.startsWith('/market/auction?')?packet:path?.startsWith('/market/auction/close-review?')?close:path==='/market/auction/automation'?automatic:path?.startsWith('/market/auction/topic-summary?')?topics:path?.startsWith('/market/auction/feedback?')?feedback:path?.startsWith('/market/auction/resonance?')?resonance:path?.startsWith('/market/auction/discovery?')?discovery:path?.startsWith('/market/auction/discovery/history?')?discoveryHistory:null,loading:false,error:'',reload(){}};}
export function api(){throw Error('Rendering never reads outside useApi');}export function post(){throw Error('Page rendering must not submit a source task');}export const requestKey=()=> 'fixture-request';`);
const uiProbe = encode(`import{jsx,jsxs}from ${JSON.stringify(runtime)};export const useToast=()=>()=>{};
export const Table=()=>null;export const Modal=({children})=>jsx('div',{children});export const Badge=({children})=>jsx('span',{children});export const Panel=({title,subtitle,children,actions})=>jsxs('section',{children:[jsx('h2',{children:title}),jsx('p',{children:subtitle}),children,actions]});
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
// These legacy one/two-stock fixtures exercise the full association view.
// Focus-mode folding is covered separately by presentation and browser checks.
function render(packet,states={},close=null,automatic=null,observedDate=day,topics=null,feedback=null,resonance=null,discovery=null,history=null){setData(packet,close,automatic,topics,feedback,resonance,discovery,history);setStates({auctionScope:"all",...states});return renderToStaticMarkup(createElement(AuctionReview,{date:observedDate,onDate(){},onStock(){},chartDate:'2026-10-28'})).replaceAll(/<!--[^]*?-->/g,'');}
let html=render(view);
assert.match(html,/自动竞价尚未开启/);
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
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",null,`/market/auction/topic-summary?date=${day}`,`/market/auction/feedback?date=${day}`,`/market/auction/discovery?date=${day}`,null,`/market/auction/resonance?date=${day}`]);
html=render({...retained,date:'2026-10-28'});
assert.doesNotMatch(html,/昨日我的原判断|98\.00万元/,'date switch must not leak prior response');
const discovered = [
  {instrument_id:'688001.SH',name:'名单外甲',auction_pct:4,auction_amount:140000,previous_board_count:2,warnings:['合成异动']},
  {instrument_id:'688002.SH',name:'名单外乙',auction_pct:2,auction_amount:120000,previous_board_count:null},
  {instrument_id:'688003.SH',name:'单只异动丙',auction_pct:-1,auction_amount:null,previous_board_count:null},
];
const discovery={date:day,state:'partial',coverage:{candidates:4,valid:3,has_more:true},directions:[{
  instrument_id:'884001.TI',name:'合成设备方向',kind:'industry',stock_count:2,median_auction_pct:3,total_auction_amount:260000,
  members_observed_date:'2026-10-24',stocks:discovered.slice(0,2),
}],stocks:discovered,supplement:{requested:1,valid:1,remaining:2,items:[{instrument_id:'688004.SH',name:'补查丁',auction_pct:1,auction_amount:0}]}};
html=render(retained,{},null,null,day,null,null,null,discovery);
assert.match(html,/昨日重点反馈/);
assert.match(html,/今日竞价发现/);
assert.match(html,/合成设备方向/);
assert.match(html,/成员档案 2026-10-24/);
assert.match(html,/来源仍有未读取的记录/);
assert.match(html,/昨日重点补查 · 核验 1 \/ 1 只/);
assert.match(html,/复制六位代码 688001/);
assert.match(html,/昨日2板/);
assert.equal((html.match(/样本领涨/g)||[]).length,1);
assert.match(html,/其他个股异动 · 1 只/);
assert.equal((html.match(/<details class="auction-discovery-extra" open=""/g)||[]).length,2,"individual anomalies and prior-focus recheck start open");
assert.equal((html.match(/主要行业与概念/g)||[]).length,2,"both independent discovery lists expose their own distribution");
assert.match(html,/-1\.00%/);
assert.match(html,/0\.00元/);
assert.match(html,/补查丁/);
assert.equal((html.match(/名单外甲/g)||[]).length,1);
// A single deferred, date-scoped history read serves all discovery tables.
const discoveryHistory={date:day,slot:'auction',as_of:'2026-10-28',items:{
 '688001.SH':{change_30d_pct:123.45,return_ytd_pct:-6.78,last_limit_up_date:'2026-10-27',last_limit_up_complete:false,limit_up_1y:null,limit_up_1y_observed:7},
 '688002.SH':{change_30d_pct:9.99,return_ytd_pct:0,limit_up_1y:0,limit_up_1y_complete:true},
}};
const detailedDiscovery={...discovery,directions:discovery.directions.map(row=>({...row,stocks:row.stocks.map(stock=>({...stock,board:'star',themes:[{name:'合成行业',kind:'industry',member_date:'2026-10-24'},{name:'合成题材',kind:'concept',member_date:'2026-10-24'}]}))}))};
html=render(retained,{},null,null,day,null,null,null,detailedDiscovery,discoveryHistory);
const discoveryTables=html.match(/<table[^>]*auction-discovery-stocks[^]*?<\/table>/g);
assert.equal(discoveryTables.length,3,'direction, individual and prior-focus discovery tables share the same columns');
for(const table of discoveryTables){
 assert.match(table,/<thead><tr><th[^>]*aria-sort="descending"[^>]*>[^]*?按竞价涨幅升序排列/,'auction change is the first column and default descending');
 for(const caption of ['股票','上市板块','行业 / 概念','昨日板数','竞价额','近30交易日涨幅','年内涨幅','最近一次涨停','近一年涨停次数','来源异动']) assert.ok(table.includes(`按${caption}`),caption+' is sortable');
}
assert.match(discoveryTables[0],/科创板/);
assert.match(discoveryTables[0],/合成行业/);
assert.match(discoveryTables[0],/合成题材/);
assert.match(discoveryTables[0],/123\.45%/);
assert.match(discoveryTables[0],/-6\.78%/);
assert.match(discoveryTables[0],/≥7 次/,'partial one-year count retains its lower bound');
assert.match(discoveryTables[0],/>0 次</,'a verified zero count remains zero');
assert.match(html,/历史指标截至 2026-10-28 收盘/);
assert.equal(requests.filter(path=>path?.startsWith('/market/auction/discovery/history?')).length,1,'one history request, no per-row network calls');
html=render(retained,{discoverySort:{key:'auction_pct',order:'asc'}},null,null,day,null,null,null,detailedDiscovery,discoveryHistory);
let firstTable=html.match(/<table[^>]*auction-discovery-stocks[^]*?<\/table>/)[0];
assert.ok(firstTable.indexOf('名单外乙')<firstTable.indexOf('名单外甲'),'auction percentage header reverses the row order');
html=render(retained,{},null,null,day,null,null,null,detailedDiscovery,{...discoveryHistory,date:'2026-10-28'});
assert.doesNotMatch(html,/123\.45%|≥7 次/,'history metrics never leak from another date');
html=render(retained,{},null,null,day,null,null,null,{...discovery,date:'2026-10-28'});
assert.doesNotMatch(html,/合成设备方向|名单外甲|补查丁/,'discovery facts never cross observation dates');
const discoveryMissing={date:day,state:'not_collected',coverage:{pages:0,candidates:0,valid:0},supplement:{requested:0,remaining:0}};
html=render({...retained,task:{status:'running',parameters:{automation:{discovery:{version:'auction-discovery-v1'}}}}},{},null,null,day,null,null,null,discoveryMissing);
assert.match(html,/昨日重点股竞价已留存，正在核对名单外的竞价异动/);
html=render(retained,{},null,null,day,null,null,null,discoveryMissing);
assert.match(html,/前往自动采集计划/);
assert.doesNotMatch(html,/名单外线索|昨日重点补查/,'missing results never show zero coverage as market facts');
html=render(retained,{},null,null,day,null,null,null,{date:day,state:'unavailable',reason:'本轮来源暂未提供数据'});
assert.match(html,/本轮来源暂未提供数据/);
assert.match(html,/昨日复盘 → 今日竞价/,'discovery failure does not hide the prior focus');
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
html=render(benchmarkSample,{4:'asc'});
assert.ok(html.indexOf('基准低开样本')<html.indexOf('基准平开样本'));
assert.ok(html.indexOf('基准平开样本')<html.indexOf('基准高开样本'));
assert.ok(html.indexOf('基准高开样本')<html.indexOf('基准未知样本'),'unknown stays last in both directions');
html=render(retained,{0:true},{date:day,state:'pending',reason:'等待同日盘后增量发布',posthoc:true});
assert.match(html,/等待同日盘后增量发布/);
assert.match(html,/盘后增量完成后自动刷新/);
assert.match(html,/读取盘后结果/,'the manual refresh fallback remains available');
assert.deepEqual(requests,[`/market/auction?date=${day}`,"/market/auction/automation",`/market/auction/close-review?date=${day}`,`/market/auction/topic-summary?date=${day}`,`/market/auction/feedback?date=${day}`,`/market/auction/discovery?date=${day}`,null,`/market/auction/resonance?date=${day}`]);
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
const automatic={version:'auction-automation-v1',revision:1,mode:'daily',date:null,instrument_ids:[],today:day,now:day+'T09:00:00+08:00',timezone:'Asia/Shanghai',start_time:'09:25:30',end_time:'09:30:00',enabled:true,next_run_at:day+'T09:25:30+08:00',can_enable:true,reason:null,last_run:null,worker:{alive:true,last_heartbeat_at_ms:Date.parse(day+'T09:00:00+08:00')}};
html=render(view,{},null,automatic);
assert.match(html,/服务自动竞价：已开启/);
assert.match(html,/每个交易日|每日自动/);
assert.match(html,/09:25:30/);
assert.match(html,/已安排今天的自动竞价/);
assert.match(html,/浏览器可以关闭/);
assert.doesNotMatch(html,/在交易日09:25后手动采集。没有留存/);
assert.match(html,/服务运行中/);
html=render({...view,date:'2026-10-28'}, {},null,automatic,'2026-10-28');
assert.match(html,/服务自动竞价：已开启/,'global schedule remains today-scoped on historical view');
assert.doesNotMatch(html,/已安排今天的自动竞价/,'historical snapshot must not be labeled as today scheduled run');
const topics={version:'auction-topic-summary-v1',date:day,state:'observed',coverage:{requested:2,classified:2,unclassified:0},groups:[{topic_code:'886001.TI',name:'合成题材甲',kind:'concept',source:'fuyao',members_observed_date:'2026-10-21',sample_count:2,valid_count:2,missing_count:0,average_auction_pct:1.5,instrument_ids:['600001.SH','000001.SZ']}]};
html=render(retained,{},null,null,day,topics);
assert.match(html,/竞价板块与个股/);
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
const joinedTopics={...topics,source_snapshot_id:'auction-proof',source_sha256:'original-proof',groups:[{...topics.groups[0],name:'合成共振题材'}]};
const joinedResonance={...resonance,source_snapshot_id:'auction-proof',source_sha256:'original-proof'};
html=render(retained,{},null,null,day,joinedTopics,null,joinedResonance);
assert.match(html,/合成共振题材/);
assert.match(html,/3.00×/);
assert.match(html,/80.00%/);
assert.match(html,/今日一字状态未核验/);
assert.match(html,/同题材跟随观察/);
assert.match(html,/按昨日最高排序/);
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
html=render(retained,{6:{date:day,key:'fuyao:concept:886001.TI'}},null,null,day,selectableTopics,feedback);
const selectedTable=html.slice(html.indexOf('<table class="auction-table"'),html.indexOf('</table>',html.indexOf('<table class="auction-table"')));
assert.match(selectedTable,/600001/);assert.doesNotMatch(selectedTable,/000001/);assert.match(html,/取消选中/);assert.match(html,/<details open=""/);assert.match(html,/data-associated="true"/);
assert.equal((html.match(/aria-pressed="true" aria-label="合成题材甲/g)||[]).length,2,'both association strips share selection');
assert.ok((html.match(/<div class="auction-association-summary"[^]*?<\/div>/g)||[]).every(strip=>!strip.includes('复制概念代码')),'summary chip is selection, not copy');
html=render(retained,{6:{date:'2026-10-28',key:'fuyao:concept:886001.TI'}},null,null,day,selectableTopics,feedback);assert.doesNotMatch(html,/取消选中/,'selection must be date-bound');
for(const direction of ['asc','desc']){
 setData(retained,null,null,null,null,{...resonance,groups:sortGroups.slice(0,2).map((row,index)=>({...row,name:index?'ZuluGroup':'AlphaGroup',top_count:index?4:2}))});
 for(const key of ['name','top_count']){
  setStates({2:{key,direction}});const result=renderToStaticMarkup(createElement(AuctionResonance,{date:day,quotes:[]}));
  assert.ok(result.indexOf(direction==='asc'?'AlphaGroup':'ZuluGroup')<result.indexOf(direction==='asc'?'ZuluGroup':'AlphaGroup'));
 }
}
html=render(retained,{},null,null,day,classifiedTopics,feedback,resonance);
assert.match(html,/aria-label="盘前竞价页内目录"/);
for(const id of ['auction-overview','auction-automation','auction-feedback','auction-discovery','auction-previous','auction-low-open','auction-topics','auction-observation','auction-benchmark','auction-close-review']){
 assert.match(html,new RegExp('aria-controls="'+id+'"'));assert.match(html,new RegExp('id="'+id+'"'));
}
const technicalView={...retained,snapshot:{...retained.snapshot,content_sha256:'a'.repeat(64),evidence_count:3,payload:{...payload,sources:[{name:'snapshot',sha256:'b'.repeat(64),requested_at:day+'T09:25:10+08:00',received_at:day+'T09:25:11+08:00'}]}}};
html=render(technicalView);
assert.doesNotMatch(html,/来源与留存证据|正文SHA256|auction-evidence|来源原件/,'technical evidence has no reading-page section or directory entry');
assert.ok(!html.includes('a'.repeat(64))&&!html.includes('b'.repeat(64)),'API evidence metadata is not rendered as product content');
html=render({...view,state:'purged',locked_for_day:true,retention:{notice:'保留28个自然日，到期随成功盘后增量清理。'}});
assert.match(html,/竞价已按保留规则清理/);assert.match(html,/只保留日任务台账和清理回执/);assert.match(html,/保留28个自然日/);assert.doesNotMatch(html,/自动竞价尚未开启/);

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

const orderRow=(id,change)=>({instrument_id:`600${id}.SH`,name:`合成排序${id}`,auction_pct:change});
const orderGroup=(key,average,items)=>({key,title:key,count:items.length,average_auction_pct:average,items});
const orderGroups=[
 orderGroup('weaken',-1,[orderRow('105',-1)]),
 orderGroup('flat_limit_up',0,[orderRow('106',0)]),
 orderGroup('repair',3,[orderRow('101',1),orderRow('102',5)]),
 orderGroup('weak_continuation',null,[orderRow('108',null)]),
 orderGroup('flat_limit_break',0,[orderRow('107',0)]),
 orderGroup('continuation',2,[orderRow('103',4),orderRow('104',0)]),
 referenceLimit,
];
const cardOrder=markup=>Array.from(markup.matchAll(/data-feedback-group="([^"]+)"/g),match=>match[1]);
const orderBefore=JSON.stringify(orderGroups);
html=renderFeedback(orderGroups);
assert.deepEqual(cardOrder(html),['limit_up_open','repair','continuation','flat_limit_up','flat_limit_break','weaken','weak_continuation'],'card means descend with zero between positive and negative, missing last, equal means stable');
html=renderFeedback(orderGroups,new Set(['600007.SH','600101.SH','600103.SH','600105.SH','600106.SH','600107.SH','600108.SH']));
assert.deepEqual(cardOrder(html),['limit_up_open','continuation','repair','flat_limit_up','flat_limit_break','weaken','weak_continuation'],'selected subset means are recomputed before ordering');
assert.match(html,/板高与行业概念可组合筛选/);
assert.match(html,/<details open=""/);assert.match(html,/data-associated="true"/);
html=renderFeedback([...orderGroups.filter(group=>group.key!=='limit_up_open'),{...referenceLimit,average_auction_pct:-100}]);
assert.equal(cardOrder(html)[0],'limit_up_open','explicit priority remains pinned regardless of reference mean');
assert.equal(JSON.stringify(orderGroups),orderBefore,'render ordering does not mutate API groups');
console.log('Auction feedback order: pinned opening reference, descending means, zero/unknown/stable ties and subset reordering passed');

const closeStock={instrument_id:'600001.SH',name:'合成盘后转弱甲',state:'observed',auction_state:'observed',auction_price:9.8,auction_pct:-2,pre_close_price:10,open:9.8,close:10.78,close_pct:7.8,auction_to_close_pct:10,open_check:'matched',reason:null};
const groupedClose={version:'auction-close-review-v1',date:day,state:'partial',posthoc:true,items:[closeStock],summary:{requested:2,valid:1,missing:1,above_auction:1,below_auction:0,flat:0,median_auction_to_close_pct:10,open_mismatch:0,average_close_pct:7.8,average_auction_to_close_pct:10},groups:[{key:'weaken',title:'昨日涨停→今日低开',items:[closeStock],count:1,average_auction_pct:-2},{key:'unknown_quote',title:'本次竞价报价未知',items:[{instrument_id:'000001.SZ',name:'合成盘后缺失乙',state:'unknown',auction_state:'unknown',auction_pct:null,close_pct:null,auction_to_close_pct:null}],count:1}]};
html=render(retained,{0:true},groupedClose);
assert.match(html,/data-close-group="weaken"/);assert.match(html,/盘前分组/);
assert.match(html,/收盘均涨幅/);assert.match(html,/竞价至收盘均变化/);assert.match(html,/\+7\.80%/);assert.match(html,/\+10\.00%/);
assert.match(html,/aria-label="全部冻结股票盘后汇总"/);
assert.doesNotMatch(html,/<th>原冻结股票<\/th>/,'the grouped comparison replaces the long ungrouped close table');
assert.match(html,/读取盘后结果/,'manual refresh remains alongside grouped automatic results');
html=render(retained,{0:true,6:{date:day,key:'fuyao:concept:886001.TI'}},groupedClose,null,day,selectableTopics);
assert.doesNotMatch(html,/aria-label="全部冻结股票盘后汇总"/,'a filtered group comparison does not mix in full-universe aggregate metrics');
assert.match(html,/合成盘后转弱甲/);assert.match(html,/data-associated="true"/);
assert.doesNotMatch(html,/合成盘后缺失乙/,'the posthoc list shares the selected frozen membership intersection');
const pendingGroups={...groupedClose,state:'pending',summary:null,items:[],groups:groupedClose.groups.map(group=>({...group,items:group.items.map(row=>({...row,state:'unknown',open:null,close:null,close_pct:null,auction_to_close_pct:null}))}))};
html=render(retained,{0:true},pendingGroups);
assert.match(html,/data-close-group="weaken"/,'the original group preview exists before close prices are published');
assert.doesNotMatch(html,/\+7\.80%|\+10\.00%/,'pending data cannot invent closing performance');
console.log('Auction grouped close integration: original groups, separate means, automatic/manual controls, selected membership and honest pending preview passed');

html=render(retained,{0:true,6:{date:day,key:'fuyao:concept:886001.TI'}},groupedClose,null,day,selectableTopics);
const groupedCloseSection=html.slice(html.indexOf('id="auction-close-review"'));
assert.match(groupedCloseSection,/aria-label="盘后行业概念收盘均幅"/);
assert.match(groupedCloseSection,/收盘均\+7\.80%/);
assert.match(groupedCloseSection,/行业 合成核验行业/);assert.match(groupedCloseSection,/概念 合成核验概念/);
assert.equal((html.match(/aria-pressed="true" aria-label="合成题材甲/g)||[]).length,3,'all three association strips share the same selected identity');

const {default:AuctionAssociationSummary}=await import(await moduleUrl(new URL('./AuctionAssociationSummary.tsx',import.meta.url)));
const sectorMembers=['600001.SH','600002.SH','600003.SH'];
const sectorView={version:'auction-topic-summary-v1',date:day,groups:[{source:'fuyao',kind:'industry',topic_code:'881001.TI',name:'合成关联行业',sample_count:3,valid_count:3,instrument_ids:sectorMembers,average_auction_pct:9,members_observed_date:'2026-10-21'}]};
const closeForSectors={version:'auction-close-review-v1',date:day,state:'partial',items:[{instrument_id:sectorMembers[0],state:'observed',close_pct:4},{instrument_id:sectorMembers[1],state:'observed',close_pct:0},{instrument_id:sectorMembers[2],state:'mismatch',close_pct:99}]};
const renderSector=close=>renderToStaticMarkup(createElement(AuctionAssociationSummary,{view:sectorView,date:day,closeReview:close}));
html=renderSector(closeForSectors);assert.match(html,/收盘均\+2\.00%/);assert.match(html,/收盘涨幅有效2\/3只/);assert.doesNotMatch(html,/9\.00%|99\.00%/,'posthoc chips use verified closing changes rather than auction or mismatch values');
for(const unavailable of [{...closeForSectors,state:'pending'},{...closeForSectors,date:'2026-10-28'}]){
 html=renderSector(unavailable);assert.match(html,/合成关联行业/);assert.match(html,/收盘均—/);assert.doesNotMatch(html,/\+2\.00%|9\.00%/);
}
console.log('Auction close affiliations: frozen per-stock labels, shared association selection and independently labelled closing means passed');

const heightPacket={...retained,snapshot:{...retained.snapshot,payload:{...payload,universe:{...payload.universe,items:[{instrument_id:'600001.SH',previous_board_count:1,reasons:['严格前日已核验涨停']},{instrument_id:'000001.SZ',previous_board_count:5,reasons:['严格前日已核验涨停']}]}}}};
html=render(heightPacket);
let heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.ok(heightTable.indexOf('>000001</code>')<heightTable.indexOf('>600001</code>'),'default observation order is yesterday height before auction return');
assert.match(heightTable,/昨日首板/);assert.match(heightTable,/昨日5板/);assert.match(html,/五板及以上/);
assert.doesNotMatch(html,/<textarea|手动采集竞价|关闭，改为手动|自定义完整代码/);
html=render(heightPacket,{7:{date:day,key:'5+'}});heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.match(heightTable,/>000001<\/code>/);assert.doesNotMatch(heightTable,/>600001<\/code>/,'height selection filters the displayed list without changing original counts');
html=render(heightPacket,{7:{date:'2026-10-28',key:'5+'}});heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.match(heightTable,/>600001<\/code>/,'yesterday selection must not leak to another auction date');
html=render(heightPacket,{6:{date:day,key:'fuyao:concept:886001.TI'},7:{date:day,key:'5+'}},null,null,day,selectableTopics,feedback);
heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.match(heightTable,/当前 0 \/ 2只/,'combined height and affiliation filters must show the actual empty intersection');
assert.doesNotMatch(heightTable,/>600001<\/code>|>000001<\/code>/,'neither condition can widen the other');
html=render(heightPacket,{6:{date:day,key:'fuyao:concept:886001.TI'},7:{date:day,key:'1'}},null,null,day,selectableTopics,feedback);
heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.match(heightTable,/>600001<\/code>/);assert.doesNotMatch(heightTable,/>000001<\/code>/);
html=render({...heightPacket,stock_boards:{'600001.SH':{board:'main',board_label:'主板',board_scope:'当前代码分类，非历史身份'}}});
heightTable=html.slice(html.indexOf('id="auction-observation"'));heightTable=heightTable.slice(0,heightTable.indexOf('</table>'));
assert.match(heightTable,/data-board="main"[^]*?>主板/,'read-only listing-board projection reaches the actual observation list');
console.log('Auction automatic-only page, date isolation, intersected affiliations/height and default dual-key sort passed');

const listQuotes=[
 {instrument_id:'600101.SH',name:'分组高板高开',state:'observed',auction_pct:1,previous_board_count:3,board:'main'},
 {instrument_id:'688101.SH',name:'分组首板强开',state:'observed',auction_pct:5,previous_board_count:1,board:'star'},
 {instrument_id:'300101.SZ',name:'分组高板低开',state:'observed',auction_pct:-1,previous_board_count:3,board:'chinext'},
 {instrument_id:'920101.BJ',name:'分组缺报样本',state:'unknown',auction_pct:99,board:'unknown'},
];
function renderStockList(states={}){
 setStates(states);
 return renderToStaticMarkup(createElement(AuctionStockTiles,{ids:listQuotes.map(r=>r.instrument_id),quotes:listQuotes,chartDate:'2026-10-28',onStock(){},topIds:new Set(['600101.SH'])})).replaceAll(/<!--[^]*?-->/g,'');
}
html=renderStockList();
assert.ok(html.indexOf('分组高板高开')<html.indexOf('分组高板低开'));
assert.ok(html.indexOf('分组高板低开')<html.indexOf('分组首板强开'),'height wins before percent');
assert.match(html,/aria-label="展开个股排序"/);assert.match(html,/aria-label="展开个股上市板块筛选"/);
assert.match(html,/当前 4 \/ 本组 4只/);assert.doesNotMatch(html,/99\.00%/);
html=renderStockList({1:{key:'change',direction:'asc'}});
assert.ok(html.indexOf('分组高板低开')<html.indexOf('分组高板高开'));
assert.ok(html.indexOf('分组首板强开')<html.indexOf('分组缺报样本'),'missing stays last in ascending change');
html=renderStockList({1:{key:'change',direction:'desc'}});
assert.ok(html.indexOf('分组首板强开')<html.indexOf('分组高板高开'));
html=renderStockList({0:'star',1:{key:'change',direction:'asc'}});
assert.match(html,/当前 1 \/ 本组 4只/);assert.match(html,/分组首板强开/);assert.doesNotMatch(html,/分组高板高开|分组高板低开|分组缺报样本/);
html=renderStockList({0:'beijing'});assert.match(html,/当前 0 \/ 本组 4只/);assert.match(html,/本组没有符合当前上市板块的股票/);
console.log('Expanded auction stock cards: independent board filters/counts, height/change sorting, safe empty state and unknown-last integration passed');
