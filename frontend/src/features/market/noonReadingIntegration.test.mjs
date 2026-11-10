import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const day = "2026-10-28";
const fixture = {
  date: day, today: day, state: "available", locked_for_day: true,
  can_capture: false, capture_window: { allowed: false },
  retention: { calendar_days: 30 },
  snapshot: { date: day, snapshot_id: "fixture-noon-reading", quality: "partial",
    workbench: { version: "noon-workbench-v1", previous_feedback: {}, today_ladder: {}, directions: {}, afternoon: [] },
    payload: { date: day, indices: [], summary: {}, events: {}, analysis: {summary:["上涨1只、下跌1只、平盘0只；上证指数+0.72%，深证成指-1.20%。"]}, previous_review: { date: "2026-10-27", state: "missing",background:{date:"2026-10-27",mode:"previous_session_price_facts",observed:5568,comparable:5562,up:604,down:4916,flat:42} },
      hot_stocks: [{ instrument_id: "600001.SH", name: "合成热榜股票", rank: 1 }, { instrument_id: "invalid", name: "未核验热榜身份", rank: 2 }] },
  },
};
const industry = {topic_code:"881001.TI", name:"合成行业",kind:"industry",members_observed_date:"2026-10-21"};
const concept = {topic_code:"885001.TI",name:"合成概念",kind:"concept",members_observed_date:"2026-10-21"};
fixture.snapshot.membership_context={stock_classifications:{"600001.SH":{industries:[industry],topics:[concept]},"600002.SH":{industries:[industry],topics:[concept]}}};
fixture.snapshot.workbench.previous_feedback.items=[{instrument_id:"600001.SH",previous_board_count:2},{instrument_id:"600002.SH",previous_board_count:5}];
fixture.snapshot.payload.events={
  limit_up:{count:2,declared_total:2,items:[{instrument_id:"600001.SH",name:"合成低板",continue_day_cnt:2,change_pct:1},{instrument_id:"600002.SH",name:"合成高板",continue_day_cnt:4,change_pct:2}]},
  limit_down:{count:1,declared_total:1,items:[{instrument_id:"600003.SH",name:"合成跌停",change_pct:-10}]},
  limit_break:{count:1,declared_total:1,items:[{instrument_id:"600003.SH",name:"合成炸板",change_pct:0,open_times:0}]},
};
fixture.snapshot.payload.previous_review.observations=[{instrument_id:"600001.SH",name:"合成低板",latest_price:10,change_pct:1},{instrument_id:"600002.SH",name:"合成高板",latest_price:20,change_pct:2}];
// Four exchange boards coexist in one frozen snapshot; filtering must not
// rebuild its breadth or discard later-ranked matches before selection.
const boardSamples = [
  {instrument_id:"688001.SH",name:"合成科创高额",latest_price:30,change_pct:3,amount:200,continue_day_cnt:3},
  {instrument_id:"688002.SH",name:"合成科创低额",latest_price:10,change_pct:1,amount:5,continue_day_cnt:1},
  {instrument_id:"300001.SZ",name:"合成创业板",latest_price:15,change_pct:2,amount:100,continue_day_cnt:1},
  {instrument_id:"920001.BJ",name:"合成北交所",latest_price:8,change_pct:1,amount:50,continue_day_cnt:1},
];
fixture.snapshot.payload.stocks = [
  ...Array.from({length:12},(_,index)=>({instrument_id:`${600010+index}.SH`,name:`合成主板额榜${index}`,latest_price:10,change_pct:1,amount:1000-index})),
  ...boardSamples,
];
fixture.snapshot.payload.summary.breadth = {up:9,down:3,flat:0,valid:12,total:12};
fixture.snapshot.payload.hot_stocks.push(
  ...Array.from({length:8},(_,index)=>({instrument_id:`${600030+index}.SH`,name:`合成主板热榜${index}`,rank:index+3})),
  ...boardSamples.map((row,index)=>({...row,rank:index+11})),
);
fixture.snapshot.payload.events.limit_up.items.push(...boardSamples);
fixture.snapshot.payload.events.limit_up.count += boardSamples.length;
fixture.snapshot.payload.events.limit_up.declared_total += boardSamples.length;
fixture.snapshot.payload.previous_review.observations.push(...boardSamples);
for (const row of boardSamples) {
  fixture.snapshot.membership_context.stock_classifications[row.instrument_id]={industries:[industry],topics:[concept]};
  fixture.snapshot.workbench.previous_feedback.items.push({...row,previous_board_count:row.continue_day_cnt});
}
fixture.snapshot.workbench.event_classification_counts = {
  limit_up:{sample_count:6,industries:[],topics:[],up_count:6,valid_count:6,mean_change_pct:10},
  limit_down:{sample_count:1,industries:[],topics:[],up_count:0,valid_count:1,mean_change_pct:-9.95},
  limit_break:{sample_count:1,industries:[],topics:[],up_count:1,valid_count:2,mean_change_pct:6.47},
};
const require = createRequire(import.meta.url);
const output = await build({
  configFile: false, root: new URL("../../../", import.meta.url).pathname, logLevel: "silent",
  plugins: [{
    name: "fixture-noon-api",
    enforce: "pre",
    resolveId(source, importer) {
      if (source.includes("lib/api") && importer?.endsWith("/NoonReview.tsx")) return "\0fixture-noon-api";
      if (source === "react" && importer?.endsWith("/NoonReview.tsx")) return "\0fixture-noon-react";
      if (source === "./useHistoricalPrefetch" && importer?.endsWith("/NoonReview.tsx")) return "\0fixture-noon-prefetch";
    },
    load(id) {
      if (id === "\0fixture-noon-react") return `import * as React from "react";
        export const useEffect=React.useEffect; export const useRef=React.useRef; export const lazy=React.lazy; export const Suspense=React.Suspense;
        export const useState=(initial)=>React.useState(initial==='all' ? globalThis.__fixtureNoonBoard || initial : initial==='overview' ? globalThis.__fixtureNoonTab || initial : initial);`;
      if (id === "\0fixture-noon-prefetch") return "export function useHistoricalPrefetch(...args){globalThis.__fixtureNoonPrefetch=args;}";
      if (id !== "\0fixture-noon-api") return;
      return `const fixture=${JSON.stringify(fixture)};
        export function useApi(path) { return {data:path.endsWith('/notes') ? {revision:0,body:''} : globalThis.__fixtureNoonView || fixture,loading:false,error:null,reload(){}}; }
        export function usePageActive(){return true;}
        export function api(){throw Error('render must not call API');}
        export const post=api; export function requestKey(){return 'fixture';}`;
    },
  }],
  build: { write: false, ssr: new URL("./NoonReview.tsx", import.meta.url).pathname, minify: false },
});
const entry = output.output.find((item) => item.type === "chunk" && item.isEntry);
const code = entry.code.replace(/from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`);
const moduleDirectory = await fs.mkdtemp(path.join(tmpdir(), "noon-reading-test-"));
const modulePath = path.join(moduleDirectory, "component.mjs");
await fs.writeFile(modulePath, code);
const { default: NoonReview } = await import(pathToFileURL(modulePath).href);
const fetchBefore = globalThis.fetch;
globalThis.fetch = () => { throw Error("reading/navigation rendering must not fetch"); };
try {
  const prefetchDates = ["2026-10-27", day];
  const props = {date:day,latestDate:day,prefetchDates,onDate(){},onStock(){},chartDate:"2026-10-27"};
  const html = renderToStaticMarkup(createElement(NoonReview, props));
  assert.deepEqual(globalThis.__fixtureNoonPrefetch, [true, `/market/noon?date=${day}`, day, "fixture-noon-reading", prefetchDates, true]);
  globalThis.__fixtureNoonTab = "close";
  renderToStaticMarkup(createElement(NoonReview, props));
  assert.equal(globalThis.__fixtureNoonPrefetch[0], false, "closing review does not trigger a full noon prefetch");
  delete globalThis.__fixtureNoonTab;
  delete globalThis.__fixtureNoonPrefetch;
  assert.equal((html.match(/保留 30 个自然日/g) || []).length, 2, "header and retention details use the same API policy");
  assert.ok(html.includes('aria-label="午评阅读导航"'));
  assert.match(html, /#ai\?ai_kind=market(?:&amp;|&)ai_day=2026-10-28(?:&amp;|&)ai_stage=noon/, "noon opens a dated AI context without sending or capturing");
  for (const id of ["noon-overview-start", "noon-prior-feedback", "noon-today-ladder", "noon-directions", "noon-afternoon", "noon-review-history"])
    assert.ok(html.includes(`id="${id}"`), `navigation target ${id} must exist in the real page`);
  assert.ok(!html.includes('aria-controls="noon-data-gaps"'), "source diagnostics must not return to the primary menu");
  assert.ok(html.includes('class="noon-tabs"') && html.includes("收盘回看"), "the additive closing tab is present without restoring the diagnostics tab");
  assert.match(html, /<details id="noon-data-gaps" class="noon-source-details">/, "source details start collapsed");
  const hot = html.match(/<ol class="noon-hot-list">([\s\S]*?)<\/ol>/)?.[1] || "";
  assert.match(hot, /<button[^>]*title="查看截至2026-10-27的已发布K线"[^>]*>合成热榜股票<\/button>/);
  assert.match(hot, /<button[^>]*disabled=""[^>]*>未核验热榜身份<\/button>/);
  assert.ok(html.includes("暂无可比数据"), "unknown same-period amounts cannot be rendered as a zero ratio");
  assert.ok(html.includes('<span class="up">上涨1只</span>') && html.includes('<span class="down">下跌1只</span>'));
  assert.ok(html.includes('<span class="up">+0.72%</span>') && html.includes('<span class="down">-1.20%</span>'));
  assert.ok(html.includes('<span class="up">604</span>') && html.includes('<span class="down">4,916</span>'), "previous background colors up/down separately");
  assert.ok(html.includes('aria-label="午评上一节"') && html.includes('aria-label="午评下一节"') && html.includes('aria-keyshortcuts="Alt+ArrowDown"'));
  const quoteTable = (name, rendered = html) => rendered.match(new RegExp(`<table class="noon-table noon-quote-table" aria-label="${name}">([\\s\\S]*?)<\\/table>`))?.[1] || "";
  const pool = quoteTable("涨停池样本");
  assert.ok(pool.includes("合成行业") && pool.includes("合成概念"), "pool details expose retained classifications");
  assert.ok(pool.indexOf("合成高板") < pool.indexOf("合成低板"), "source heights default to descending order");
  assert.ok(pool.includes('aria-sort="descending"') && pool.includes('class="noon-sort-button"'));
  const downPool = quoteTable("跌停池样本");
  assert.ok(!downPool.includes("昨日高度") && !downPool.includes("最近跌停时间") && !downPool.includes("行业 / 概念"), "entirely unknown optional columns are absent");
  const breakPool = quoteTable("炸板池样本");
  assert.ok(breakPool.includes("开板次数") && breakPool.includes('>0</td>'), "a known zero keeps its column");
  const watched = quoteTable("昨日关注股票午间表现");
  assert.ok(watched.includes("合成行业") && watched.includes("合成概念") && watched.includes("昨日高度"));
  assert.ok(watched.indexOf("合成高板") < watched.indexOf("合成低板"), "original watch stocks use verified yesterday height, not today's");
  const poolSummary = (title, rendered=html) => [...rendered.matchAll(/<summary>([\s\S]*?)<\/summary>/g)].find(match=>match[1].includes(title))?.[1] || "";
  const plain = value => value.replace(/<[^>]+>/g, "");
  assert.ok(plain(poolSummary("跌停池样本")).includes("平均涨跌幅 -9.95%（有效1）"), "show the mean price change, never the up-count percentage");
  assert.ok(plain(poolSummary("炸板池样本")).includes("平均涨跌幅 +6.47%（有效2）"));
  assert.ok(!poolSummary("涨停池样本").includes("平均涨跌幅"));
  assert.ok(!html.includes("红盘率"));
  assert.ok(html.includes('aria-label="午评上市板块筛选"'));
  for (const title of ["主板", "创业板", "科创板", "北交所"]) assert.ok(html.includes(title), `board tag/filter ${title} is visible`);
  globalThis.__fixtureNoonBoard="star";
  const starHtml=renderToStaticMarkup(createElement(NoonReview,{date:day,onDate(){},onStock(){},chartDate:"2026-10-27"}));
  const starPool=quoteTable("涨停池样本",starHtml), starWatched=quoteTable("昨日关注股票午间表现",starHtml), starAmount=quoteTable("午间成交额前列股票",starHtml);
  for (const [name,table] of Object.entries({starPool,starWatched,starAmount})) {
    assert.ok(table.includes("合成科创高额") && table.includes("合成科创低额"), `the same controlled board selection filters every stock table: ${name} (${table.length} bytes)`);
    assert.ok(!table.includes("合成高板") && !table.includes("合成创业板") && !table.includes("合成北交所"));
  }
  assert.ok(starAmount.indexOf("合成科创高额") < starAmount.indexOf("合成科创低额"), "sort and take the leading ten only after filtering the complete selected-board universe");
  const starHot=starHtml.match(/<ol class="noon-hot-list">([\s\S]*?)<\/ol>/)?.[1] || "";
  assert.ok(starHot.includes("合成科创高额") && !starHot.includes("合成主板热榜") && !starHot.includes("未核验热榜身份"), "hot-list filtering retains provider order and finds matches beyond the unfiltered first ten");
  assert.ok(starHtml.includes("该板别暂无展示样本"), "a filtered empty pool is distinct from an uncollected source");
  assert.ok(starHtml.includes('aria-label="上涨9只、下跌3只、平盘0只"'), "board filtering cannot recalculate frozen market breadth");
  assert.ok(plain(poolSummary("炸板池样本",starHtml)).includes("平均涨跌幅 +6.47%（有效2）"), "board selection retains the mean and count for the complete pool");
  globalThis.__fixtureNoonView=structuredClone(fixture);
  Object.assign(globalThis.__fixtureNoonView.snapshot.workbench.event_classification_counts.limit_down,{mean_change_pct:null,up_count:null,valid_count:null});
  const unknownRate=renderToStaticMarkup(createElement(NoonReview,{date:day,onDate(){},onStock(){},chartDate:"2026-10-27"}));
  assert.ok(plain(poolSummary("跌停池样本",unknownRate)).includes("平均涨跌幅 —（有效—）"), "absent change evidence is not zero-filled");

} finally {
  globalThis.fetch = fetchBefore;
  delete globalThis.__fixtureNoonBoard;
  delete globalThis.__fixtureNoonView;
  await fs.rm(moduleDirectory, {recursive:true,force:true});
}
console.log("noon reading integration: real anchors, collapsed diagnostics, hot-list chart access and unknown baselines passed");
