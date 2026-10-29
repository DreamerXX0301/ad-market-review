import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
const encode=value=>'data:text/javascript;base64,'+Buffer.from(value).toString('base64');
const cache=new Map();
async function moduleUrl(url){
 if(cache.has(url.href))return cache.get(url.href);
 const pending=(async()=>{
  let source=ts.transpileModule(await fs.readFile(url,'utf8'),{fileName:url.pathname,compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  source=source.replace(/^import ["'][^"']+\.css["'];?$/gm,'');
  for(const name of [...new Set([...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match=>match[1]))]){
   let target;
   if(name.startsWith('.'))for(const suffix of ['.ts','.tsx']){const candidate=new URL(name+suffix,url);try{await fs.access(candidate);target=await moduleUrl(candidate);break;}catch(error){if(error.code!=='ENOENT')throw error;}}
   target ||= import.meta.resolve(name);
   source=source.replaceAll('from "'+name+'"','from '+JSON.stringify(target)).replaceAll("from '"+name+"'",'from '+JSON.stringify(target));
  }
  return encode(source);
 })();cache.set(url.href,pending);return pending;
}
const {default:AuctionCloseGroups}=await import(await moduleUrl(new URL('./AuctionCloseGroups.tsx',import.meta.url)));
const date='2026-10-29';
const data={version:'auction-close-review-v1',date,state:'partial',groups:[
 {key:'repair',state:'observed',title:'昨日炸板→今日高开',items:[{instrument_id:'000001.SZ',name:'合成盘后修复股',state:'observed',auction_pct:2,close_pct:3,auction_to_close_pct:1,open:10,close:10.1}]},
 {key:'limit_up_open',state:'reference',title:'涨停价高开参考',items:[{instrument_id:'600001.SH',name:'合成盘后参考股',state:'mismatch',auction_pct:10,close_pct:987.65,auction_to_close_pct:876.54,open:10.5,close:11,reason:'开盘价不一致，保留同日事实'}]},
 {key:'unknown_event',title:'昨日事件未知',items:[{instrument_id:'600002.SH',name:'合成未知事件股',state:'unknown',auction_pct:0,close_pct:null,auction_to_close_pct:null,reason:'缺少同日报价'}]},
]};
const render=(view,selectedIds=null,topicView=null)=>renderToStaticMarkup(createElement(AuctionCloseGroups,{date,view,topicView,selectedIds,chartDate:date,onStock(){}})).replaceAll(/<!--[^]*?-->/g,'');
let html=render(data);
assert.match(html,/盘前分组/);assert.match(html,/收盘均涨幅/);assert.match(html,/竞价至收盘均变化/);
assert.ok(html.indexOf('data-close-group="limit_up_open"')<html.indexOf('data-close-group="repair"'));
assert.match(html,/一字涨停开/);assert.match(html,/>参考<\/small>/);assert.match(html,/前日事件未知/);
assert.doesNotMatch(html,/合成盘后修复股|合成盘后参考股|合成未知事件股/,'no selection starts with details collapsed');
html=render(data,new Set(['000001.SZ','600001.SH','600002.SH']));
assert.match(html,/合成盘后修复股/);assert.match(html,/合成盘后参考股/);assert.match(html,/合成未知事件股/);
assert.match(html,/aria-expanded="true"/);assert.match(html,/data-associated="true"/);
assert.match(html,/复制六位代码 000001/);assert.match(html,/复制股票名称 合成盘后参考股/);
assert.match(html,/10\.50/);assert.match(html,/11\.00/);assert.match(html,/开盘价不一致，保留同日事实/);
assert.doesNotMatch(html,/987\.65|876\.54/,'mismatch facts remain visible while invalid returns stay unknown');
assert.match(html,/colSpan|colspan="6"/);assert.match(html,/auction-close-group-detail-scroll/);
html=render({...data,date:'2026-10-28'},new Set(['000001.SZ']));
assert.match(html,/尚未取得所选日期/);assert.doesNotMatch(html,/合成盘后修复股|\+3\.00%/);
html=render({...data,groups:[{key:'limit_up_open',state:'unknown',count:null,items:[]},...data.groups.filter(g=>g.key!=='limit_up_open')]},new Set(['000001.SZ']));
assert.match(html,/待核验/);assert.match(html,/\+3\.00%/);assert.doesNotMatch(html,/NaN/);
html=render({...data,state:'pending'},new Set(['000001.SZ']));
assert.match(html,/收盘涨幅相对来源昨收；竞价后变化相对竞价价/);assert.match(html,/待发布/);
assert.doesNotMatch(html,/\+3\.00%|收盘 0\/1|变化 0\/1|10\.10/,'pending publication preserves the pre-open group without pretending close quotes are missing or available');
const css=await fs.readFile(new URL('./AuctionCloseGroups.css',import.meta.url),'utf8');
assert.match(css,/table-layout:\s*fixed/);assert.match(css,/min-width:\s*0/);assert.match(css,/overflow:\s*auto/);
assert.match(css,/\.auction-close-group-row\.limit\s*\{\s*--close-group-color:\s*var\(--red\)/);
const classifications={version:'auction-topic-summary-v1',date,groups:[],stock_classifications:{'000001.SZ':{industries:[{source:'fuyao',topic_code:'881001.TI',name:'合成盘后行业',members_observed_date:'2026-10-21'}],topics:[{source:'fuyao',topic_code:'886001.TI',name:'合成盘后概念',members_observed_date:'2026-10-21'}]}}};
html=render(data,new Set(['000001.SZ']),classifications);
assert.match(html,/行业 合成盘后行业/);assert.match(html,/概念 合成盘后概念/);
assert.match(html,/查看完整行业概念、竞价均幅与代码/);assert.match(html,/复制六位代码 000001/);
for(const stale of [{...classifications,date:'2026-10-28'},{...classifications,version:'other-version'}]){
 html=render(data,new Set(['000001.SZ']),stale);
 assert.doesNotMatch(html,/合成盘后行业|合成盘后概念/,'posthoc rows cannot borrow another date or version of membership');
 assert.match(html,/行业 待核验/);assert.match(html,/概念 待核验/);
}
console.log('Auction close grouped table: first reference row, folded defaults, selected expansion, preserved mismatch facts, stock name/code copying and bounded detail layout passed');
