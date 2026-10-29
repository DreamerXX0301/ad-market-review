import assert from "node:assert/strict";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const output = await build({ configFile:false,root:new URL("../../../",import.meta.url).pathname,logLevel:"silent",
  plugins:[{name:"focus-sort-state",enforce:"pre",resolveId(source,importer){
    if(source==="react" && importer?.endsWith("/NoonCloseFocusTable.tsx"))return "\0focus-sort-react";
  },load(id){if(id==="\0focus-sort-react")return 'import * as React from "react"; export const useState=initial=>React.useState(globalThis.__focusSort || initial);';}}],
  build:{write:false,ssr:new URL("./NoonCloseFocusTable.tsx",import.meta.url).pathname,minify:false}});
const entry=output.output.find(item=>item.type==="chunk"&&item.isEntry);
const require=createRequire(import.meta.url);
const code=entry.code.replace(/from "([^".][^"]*)"/g,(_,id)=>"from "+JSON.stringify(pathToFileURL(require.resolve(id)).href));
const {default:Focus,closeFocusRows}=await import("data:text/javascript;base64,"+Buffer.from(code).toString("base64"));
const rows=[
  {instrument_id:"000001.SZ",name:"合成原榜一",rank:1,noon_change_pct:10,close_change_pct:4.2,afternoon_change_pct:-5.27,noon_price:11,close_price:10.42,close_state:"not_listed",transition:"opened"},
  {instrument_id:"600002.SH",name:"合成原榜二",rank:2,noon_change_pct:2,close_change_pct:10,afternoon_change_pct:7.84,noon_price:10.2,close_price:11,close_state:"limit_up",transition:"resealed",final_board_count:3},
  {instrument_id:"600003.SH",name:"合成平盘",rank:3,noon_change_pct:-1,close_change_pct:0,afternoon_change_pct:1.01,noon_price:9.9,close_price:10},
  {instrument_id:"600004.SH",name:"合成负反馈",rank:4,noon_change_pct:2,close_change_pct:-4,afternoon_change_pct:-5.88,noon_price:10.2,close_price:9.6},
  {instrument_id:"600005.SH",name:"合成未留存",rank:null,noon_change_pct:5,close_change_pct:null,afternoon_change_pct:null,noon_price:10.5,close_price:null},
];
const before=structuredClone(rows);
const render=()=>renderToStaticMarkup(createElement(Focus,{rows,date:"2026-10-28",label:"合成固定榜单回看",ranked:true,onStock(){}}));
const table=render();
const plain=table.replace(/<[^>]+>/g,"");
for(const title of ["午间原排名","午间涨幅","今日收盘涨幅","午后涨跌幅","午间价 → 收盘价","收盘状态"])assert.ok(plain.includes(title));
assert.ok(table.includes('class="noon-close-day-change up">+4.20%</td>'));
assert.ok(table.includes('class="down">-5.27%</td>'),"positive daily return and negative afternoon return have independent colors");
assert.ok(table.includes('class="noon-close-day-change muted">0.00%</td>'));
assert.ok(table.includes('class="noon-close-day-change down">-4.00%</td>'));
assert.ok(table.includes('class="noon-close-day-change muted">—</td>'));
assert.ok(table.includes("查看截至2026-10-28的已发布收盘K线"));
assert.ok(table.indexOf("合成原榜一")<table.indexOf("合成原榜二"),"default keeps frozen original ranking");
for(const direction of ["asc","desc"]){
 const sort={key:"close_change_pct",direction};
 const sorted=closeFocusRows(rows,sort);
 assert.equal(sorted.at(-1).instrument_id,"600005.SH","null remains last in either direction");
 assert.deepEqual(sorted.slice(0,-1).map(row=>row.close_change_pct),direction==="desc"?[10,4.2,0,-4]:[-4,0,4.2,10]);
 globalThis.__focusSort=sort;
 const ordered=render();
 assert.ok(ordered.includes('aria-sort="'+(direction==="desc"?"descending":"ascending")+'"'));
 assert.ok(ordered.indexOf(direction==="desc"?"合成原榜二":"合成负反馈")<ordered.indexOf("合成平盘"));
 assert.ok(ordered.includes('>1</td>')&&ordered.includes('>2</td>'),"sorting never renumbers original noon positions");
}
delete globalThis.__focusSort;
const legacy=renderToStaticMarkup(createElement(Focus,{rows:[{instrument_id:"000001.SZ",name:"合成旧摘要",noon_price:10,close_price:11,afternoon_change_pct:10}],date:"2026-10-28",label:"旧摘要"}));
assert.ok(legacy.includes('class="noon-close-day-change muted">—</td>'),"old summary has no invented daily return from the afternoon ratio");
assert.deepEqual(rows,before,"view sorting never mutates frozen response/ranks");
console.log("Closing focus lists: explicit daily/noon/afternoon columns, signed/zero/unknown, both sort orders null-last, frozen ranks, same-day K links and immutable rows passed");
