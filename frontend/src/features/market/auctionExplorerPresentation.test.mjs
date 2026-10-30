import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const modules = new Map();
async function moduleUrl(url) {
  if (modules.has(url.href)) return modules.get(url.href);
  let js = ts.transpileModule(await fs.readFile(url,'utf8'), {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText;
  for (const name of [...new Set([...js.matchAll(/from\s+["']([^"']+)["']/g)].map(match=>match[1]))]) {
    js=js.replaceAll('from "'+name+'"','from '+JSON.stringify(await moduleUrl(new URL(name+'.ts',url))));
  }
  const result='data:text/javascript;base64,' + Buffer.from(js).toString('base64');modules.set(url.href,result);return result;
}
const {auctionExplorerGroups: groups, auctionPeerObservation: peers, auctionFocusedDirections: focused} = await import(await moduleUrl(new URL('./auctionExplorerPresentation.ts',import.meta.url)));
const day='2026-10-29';
const member={source:'source-a',kind:'concept',topic_code:'001',members_observed_date:day,source_snapshot_id:'members-a',source_sha256:'member-proof',name:'同名方向',sample_count:3,valid_count:2,instrument_ids:['a','b','c']};
const topics={date:day,version:'auction-topic-summary-v1',source_snapshot_id:'auction-a',source_sha256:'auction-proof',groups:[member]};
const resonance={date:day,version:'auction-resonance-v1',source_snapshot_id:'auction-a',source_sha256:'auction-proof',groups:[{...member,top_count:2,concentration_lift:0,up_ratio:0,median_auction_pct:0}]};
const before=structuredClone({topics,resonance});
assert.equal(groups(topics,resonance,day)[0].concentration_lift,0);
for(const field of ['source','kind','topic_code','members_observed_date','source_snapshot_id','source_sha256']) {
  const changed={...resonance,groups:[{...resonance.groups[0],[field]:'other'}]};
  assert.equal(groups(topics,changed,day)[0].top_count,null,field);
}
for(const field of ['date','source_snapshot_id','source_sha256','version']) assert.equal(groups(topics,{...resonance,[field]:'other'},day)[0].top_count,null,field);
assert.deepEqual(groups({...topics,date:'2026-10-28'},resonance,day),[]);
assert.equal(groups(topics,null,day)[0].sample_count,3,'missing resonance cannot erase verified association');
assert.equal(groups(topics,null,day)[0].top_count,null,'missing resonance cannot become zero');
assert.deepEqual({topics,resonance},before);
console.log('Merged auction: exact source and date joins, valid zero, missing evidence and immutable original counts passed');

const ids=['601956.SH','600001.SH','600002.SH','600003.SH','600004.SH','600005.SH'];
const group={...member,instrument_ids:[...ids,ids[1]],sample_count:6};
const quote=(i,height,pct,extra={})=>({instrument_id:ids[i],previous_state:'limit_up',previous_board_count:height,auction_pct:pct,state:'observed',...extra});
const quotes=[quote(0,2,9.99,{board_count:3}),quote(1,1,4.5),quote(2,1,0),quote(3,3,-2),quote(4,null,8,{previous_state:'limit_break'})];
const withHeights=groups({...topics,groups:[group]},null,day,quotes)[0];
assert.equal(withHeights.previous_max_height,3);
assert.equal(groups({...topics,groups:[{...group,instrument_ids:[ids[0],ids[1]]}]},null,day,quotes)[0].previous_max_height,2,'today third-board evidence cannot raise yesterday height');
assert.equal(withHeights.previous_height_known_count,4,'duplicate member identities counted once');
assert.equal(withHeights.previous_height_unknown_count,1,'missing quote stays unknown; broken board is a separate known event');
assert.equal(groups({...topics,groups:[group]},null,day,[])[0].previous_max_height,null);
assert.equal(groups({...topics,groups:[{...group,instrument_ids:[ids[0]]}]},null,day,[quotes[0],quote(0,4,9.99)])[0].previous_max_height,null,'conflicting heights do not pick a winner');
assert.equal(groups({...topics,groups:[{...group,instrument_ids:[ids[0]]}]},null,day,[quote(0,5,9.99,{reasons:['严格前日已核验炸板']})])[0].previous_max_height,null,'broken evidence cannot create a high ladder');
assert.equal(groups({...topics,groups:[{...group,instrument_ids:[ids[0]]}]},null,day,[quote(0,5,9.99),quote(0,5,9.99,{reasons:['严格前日已核验炸板']})])[0].previous_max_height,null,'duplicate event contradiction cannot keep the first winning row');
const beforeQuotes=structuredClone(quotes);
const observed=peers(group,quotes,ids[0]);
assert.equal(observed.count,5);assert.equal(observed.valid_count,4);assert.equal(observed.missing_count,1);
assert.deepEqual([observed.up_count,observed.flat_count,observed.down_count],[2,1,1]);
assert.equal(observed.up_ratio,.5);assert.equal(observed.median_auction_pct,2.25);
assert.deepEqual(observed.lower_up_ids,[ids[1]],'only lower verified board and positive observed quote');
assert.equal(observed.lower_count,2);assert.equal(observed.lower_valid_count,2);
assert.deepEqual([observed.first_count,observed.second_count,observed.higher_count,observed.broken_count,observed.other_count],[2,0,1,1,1]);
assert(!observed.instrument_ids.includes(ids[0]),'anchor excluded from every peer denominator');
assert.equal(peers(group,quotes,'600999.SH'),null);
assert.equal(peers(group,quotes.map(row=>({...row,state:'unknown'})),ids[0]).median_auction_pct,null);
assert.equal(peers(group,quotes.map(row=>({...row,previous_board_count:null})),ids[0]).lower_count,null);
assert.equal(peers({...group,instrument_ids:[ids[0]]},quotes,ids[0]).up_ratio,null,'isolated anchor has no fabricated breadth');
assert.deepEqual(quotes,beforeQuotes);
console.log('Frozen prior-day maximum and manual-reference peers: anchor exclusion, deduplication, unknowns, flat/negative quotes, lower ladder and immutable data passed');

const focusSource=[
 {...member,name:'融资融券',sample_count:59},
 {...member,name:'深股通',topic_code:'002',sample_count:44},
 {...member,name:'通用设备',kind:'industry',topic_code:'003',sample_count:4},
 {...member,name:'比亚迪概念',topic_code:'004',sample_count:27},
 {...member,name:'商业航天',topic_code:'005',sample_count:11},
 {...member,name:'单股方向',topic_code:'006',sample_count:1},
];
const originalFocus=structuredClone(focusSource), focus=focused(focusSource);
assert.deepEqual(focus.groups.map(row=>row.name),['通用设备','比亚迪概念','商业航天']);
assert.equal(focus.attribute_count,2);assert.equal(focus.small_count,1);
assert.deepEqual(focusSource,originalFocus,'display scope never erases original catalog or rewrites stats');
console.log('Focus display: exact general attributes folded, sparse groups folded, genuine industries/themes retained');
