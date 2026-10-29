import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import ts from 'typescript';
const source=await fs.readFile(new URL('./auctionClosePresentation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {auctionCloseGroupsView,auctionCloseNumber}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const date='2026-10-29';
const packet=(groups,state='available')=>({version:'auction-close-review-v1',date,state,groups});
const row=(id,auction,close,change,state='observed')=>({instrument_id:id,name:id,auction_price:10,auction_pct:auction,close_pct:close,auction_to_close_pct:change,state});
const group=(key,items,state='observed')=>({key,title:key,state,items,count:items.length});
const pending={key:'limit_up_open',state:'unknown',count:null,items:[],average_auction_pct:null};
const input=packet([
 group('repair',[row('000001.SZ',1,15,9),row('000002.SZ',3,999,999,'mismatch'),row('000003.SZ',null,999,999,'unknown')]),
 group('weaken',[row('600001.SH',-2,90,90)]),
 group('continuation',[row('600002.SH',6,-50,-50)]),
 group('unknown_event',[row('600003.SH',20,2,2)]),
 pending,
]);
const before=JSON.stringify(input);const view=auctionCloseGroupsView(date,input);
assert.deepEqual(view.map(g=>g.key),['limit_up_open','continuation','repair','weaken','unknown_event'],'first limit group and neutral unknown groups remain separate; other ranking uses pre-open mean');
const repair=view.find(g=>g.key==='repair');
assert.equal(repair.average_auction_pct,2);assert.equal(repair.average_close_pct,15);assert.equal(repair.average_auction_to_close_pct,9);
assert.equal(repair.valid_count,1);assert.equal(repair.close_valid_count,1);assert.equal(repair.missing_count,2);assert.equal(repair.open_mismatch,1);
assert.equal(repair.items[1].display_close_pct,null);assert.equal(repair.items[1].display_change_pct,null);
assert.equal(view[0].count,null);assert.equal(view[0].average_close_pct,null);
assert.equal(view.at(-1).tone,'neutral');assert.equal(view.at(-1).items[0].instrument_id,'600003.SH','unknown events retain the actual stock');
const selected=auctionCloseGroupsView(date,input,new Set(['000002.SZ','000003.SZ']));
assert.deepEqual(selected.map(g=>g.key),['limit_up_open','repair']);assert.equal(selected[1].count,2);assert.equal(selected[1].average_auction_pct,3);
assert.equal(selected[1].average_close_pct,null);assert.equal(selected[1].average_auction_to_close_pct,null);assert.equal(selected[1].valid_count,0);
assert.equal(selected[0].count,null,'shared selection must not turn missing limit evidence into zero');
const reference=auctionCloseGroupsView(date,packet([group('limit_up_open',[row('600004.SH',10,0,0)],'reference'),group('repair',[row('600005.SH',20,8,8)])]),new Set(['600005.SH']));
assert.equal(reference[0].key,'limit_up_open');assert.equal(reference[0].count,0);assert.equal(reference[0].state,'reference');assert.equal(reference[0].average_auction_pct,null);
const zeros=auctionCloseGroupsView(date,packet([group('repair',[row('600006.SH',0,0,0)])]));
assert.equal(zeros[0].average_auction_pct,0);assert.equal(zeros[0].average_close_pct,0);assert.equal(zeros[0].average_auction_to_close_pct,0);
const closeMissing=auctionCloseGroupsView(date,packet([group('repair',[row('600006.SH',2,null,3)])]));
assert.equal(closeMissing[0].valid_count,1);assert.equal(closeMissing[0].close_valid_count,0);
const waiting=auctionCloseGroupsView(date,packet([group('repair',[row('600006.SH',2,null,null,'unknown')])],'pending'));
assert.equal(waiting[0].count,1);assert.equal(waiting[0].average_auction_pct,2);assert.equal(waiting[0].average_close_pct,null);assert.equal(waiting[0].valid_count,0);
for(const value of [null,{}, {...input,date:'2026-10-28'}, {...input,version:'other'}, {...input,groups:null}])assert.equal(auctionCloseGroupsView(date,value),null);
for(const value of [null,'0',true,NaN,Infinity])assert.equal(auctionCloseNumber(value),null);
assert.equal(JSON.stringify(input),before,'projection and filtering never edit original packets');
const explicitUnknown=auctionCloseGroupsView(date,packet([group('repair',[{...row('600006.SH',999,3,2),auction_state:'unknown'},row('600007.SH',0,0,0)])]));
assert.equal(explicitUnknown[0].items[0].display_auction_pct,null);assert.equal(explicitUnknown[0].average_auction_pct,0,'legacy rows without auction_state remain compatible but explicit unknown is rejected');
const tied=auctionCloseGroupsView(date,packet([group('continuation',[row('600006.SH',2,99,90)]),group('repair',[row('600007.SH',2,-99,-90)]),group('weaken',[row('600008.SH',2,1,1)])]));
assert.deepEqual(tied.map(g=>g.key),['repair','weaken','continuation'],'equal pre-open means use canonical group order, never backend order or close outcome');
const neutral=auctionCloseGroupsView(date,packet([group('unknown_event',[row('600006.SH',-9,9,9)]),group('unknown_quote',[row('600007.SH',99,9,9)]),group('repair',[row('600008.SH',0,0,0)])]));
assert.deepEqual(neutral.map(g=>g.key),['repair','unknown_event','unknown_quote'],'unknown event groups remain neutral at the end in source order');
const pendingFacts=auctionCloseGroupsView(date,packet([group('repair',[{...row('600006.SH',2,999,999),close:999}])],'pending'));
assert.equal(pendingFacts[0].average_close_pct,null);assert.equal(pendingFacts[0].average_auction_to_close_pct,null);assert.equal(pendingFacts[0].items[0].display_close_price,null);assert.equal(pendingFacts[0].pending_close,true);
console.log('Auction close groups: pre-open ordering, shared subset means, unknown/mismatch exclusion, honest zeros, pending groups and date isolation passed');
