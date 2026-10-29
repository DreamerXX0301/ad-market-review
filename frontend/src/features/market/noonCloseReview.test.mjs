import assert from "node:assert/strict";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = new URL("../../../", import.meta.url).pathname;
const output = await build({ configFile: false, root, logLevel: "silent",
  plugins: [{ name: "fixture-close-view", enforce: "pre",
    resolveId(source, importer) {
      if (source.includes("lib/api") && importer?.endsWith("/NoonCloseReview.tsx")) return "\0fixture-close-api";
    },
    load(id) {
      if (id !== "\0fixture-close-api") return;
      return "export function usePageActive(){return true;} export function api(){throw Error('render does not submit or collect');} export function useApi(path){globalThis.__closePaths.push(path);return {data:path?.includes('/items?')?globalThis.__closeItems:globalThis.__closeView,loading:false,error:null,reload(){}};}";
    },
  }],
  build: { write: false, ssr: new URL("./NoonCloseReview.tsx", import.meta.url).pathname, minify: false },
});
const entry = output.output.find(item => item.type === "chunk" && item.isEntry);
const require = createRequire(import.meta.url);
const code = entry.code.replace(/from "([^".][^"]*)"/g, (_, id) => "from " + JSON.stringify(pathToFileURL(require.resolve(id)).href));
const { default: CloseReview, CloseRowsTable, CloseMetricStrip, CloseObservation } = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
const day = "2026-10-28";
const row = { instrument_id: "000001.SZ", name: "合成核验股", board: "main", previous_board_count: 4, final_board_count: null,
  noon_state: "limit_up", close_state: "not_listed", transition: "opened", noon_price: 11, close_price: 10.42,
  noon_change_pct: 10, close_change_pct: 4.2, afternoon_change_pct: -5.272727, close_amount: 2000000,
  industries: [{ name: "合成行业", topic_code: "881001.TI", members_observed_date: "2026-10-21" }], topics: [],
};
const table = (rows, availableFields) => renderToStaticMarkup(createElement(CloseRowsTable, { rows, availableFields, date: day, onStock(){}, sort: "afternoon_change_pct", direction: "desc", onSort(){} }));
const opening = table([row]);
assert.match(opening, /class="down">午间封板 → 收盘未封/);
assert.match(opening, /class="noon-close-day-change up">\+4\.20%/);
assert.match(opening, /class="down">-5\.27%/);
assert.ok(opening.includes("查看截至2026-10-28的已发布收盘K线"));
assert.ok(opening.includes("000001") && opening.includes("主板") && opening.includes("合成行业"));
assert.ok(opening.includes('aria-sort="descending"'));
const bodyCells=[...opening.matchAll(/<td([^>]*)>([\s\S]*?)<\/td>/g)];
assert.equal(bodyCells.length,8,"all eight columns stay aligned");
assert.ok(bodyCells[4][2].includes("+10.00%") && bodyCells[5][2].includes("+4.20%") && bodyCells[6][2].includes("-5.27%"),"noon, final daily and afternoon values are not swapped");

const headers = [...opening.matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)];
for (const title of ["午间涨幅", "今日收盘涨幅", "午后涨跌幅"]) assert.equal(headers.filter(match => match[2].includes(title)).length, 1, title + " has its own header");
assert.ok(headers.find(match => match[2].includes("今日收盘涨幅"))[2].includes("收盘回看按今日收盘涨幅降序"));
const finalSorted = renderToStaticMarkup(createElement(CloseRowsTable, { rows:[row], date:day, sort:"close_change_pct", direction:"asc", onSort(){} }));
const finalHeaders = [...finalSorted.matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)];
assert.equal(finalHeaders.filter(match => match[1].includes("aria-sort")).length,1,"only the actual final-return column owns the sort indicator");
assert.ok(finalHeaders.find(match=>match[2].includes("今日收盘涨幅"))[1].includes('aria-sort="ascending"'));
assert.ok(!finalHeaders.find(match=>match[2].includes("午间 → 收盘"))[1].includes("aria-sort"),"event status is not a numeric return sort header");

const flat = table([{ ...row, close_state: "limit_up", transition: "kept", final_board_count: 5, close_price: 11, close_change_pct: 10, afternoon_change_pct: 0 }]);
assert.match(flat, /class="muted">0\.00%/);
assert.ok(flat.includes("收盘5板") && !flat.includes("确认断板"));
const heightConflict=table([{...row,close_state:"limit_up",transition:"resealed",relay_state:"height_conflict",final_board_count:7}]);
assert.ok(heightConflict.includes("来源7板 · 关系待核验") && heightConflict.includes('class="close-warn"'));
const missing = table([{ ...row, close_state: "unknown", transition: "unknown", close_price: null, close_change_pct: null, afternoon_change_pct: null }]);
assert.match(missing, /class="close-warn">结果待核验/);
assert.ok(!missing.includes(">0.00%</"), "missing closing prices never become zero return");
assert.ok(missing.includes('class="noon-close-day-change muted">—</td>'), "missing final daily return stays explicitly visible as unknown");
const unavailableHeaders=[...table([{...row,noon_change_pct:null,close_change_pct:null}],[]).matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)];
assert.ok(!unavailableHeaders.find(match=>match[2].includes("今日收盘涨幅"))[1].includes("hidden"),"a wholly unknown final return still has a clear place, not silently omitted");
const stockObservation=renderToStaticMarkup(createElement(CloseObservation,{date:day,onStock(){},row:{label:"合成股票观察",text:"原文不自动判定",objective:[row]}}));
assert.ok(stockObservation.includes('aria-label="原关注客观收盘结果"') && stockObservation.includes("今日收盘涨幅") && stockObservation.includes('class="noon-close-day-change up">+4.20%</td>'));

const missingPage = [{ ...row, close_state:"unknown", transition:"unknown", afternoon_change_pct:null, close_amount:null }];
const fullScopeField = table(missingPage,["afternoon_change_pct"]);
const column = [...fullScopeField.matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)].find(match=>match[2].includes("午后涨跌幅"));
assert.ok(column && !column[1].includes("hidden"), "available fields come from the complete filtered range, so a missing current page never hides a globally present column");
const absentScopeField = table(missingPage,[]);
const absentColumn = [...absentScopeField.matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)].find(match=>match[2].includes("午后涨跌幅"));
assert.ok(absentColumn?.[1].includes("hidden"), "a wholly unavailable optional field is hidden");

const metric = renderToStaticMarkup(createElement(CloseMetricStrip, { metrics: {
  kept: { count: 1, source_count: 3, denominator: 2, unknown_count: 1, rate_pct: 50 },
  resealed: { count: 0, source_count: 1, denominator: 1, unknown_count: 0, rate_pct: 0 },
  new_limit_up: { count: 81, source_count: 600, denominator: 600, unknown_count: 0, rate_pct: 13.46 },
} }));
assert.ok(metric.includes("原名单 3") && metric.includes("待核验 1") && metric.includes("50.00%"));
assert.ok(!metric.includes("+50.00%") && metric.includes("0.00%"), "ratio and return labels remain distinct");
assert.ok(metric.includes("81") && !metric.includes("13.46%"), "newly sealed focus names show count, not a rate against union-of-focus population");
const observation = renderToStaticMarkup(createElement(CloseObservation,{date:day,onStock(){},onSector(){},row:{label:"合成板块观察",rule_version:"noon-workbench-v1",text:"核对板块午后变化",instrument_ids:[],sector_objective:{topic_code:"881001.TI",kind:"industry",name:"合成行业",noon_change_pct:3,close_change_pct:2,afternoon_change_pct:-0.97}}}));
assert.ok(observation.includes("午间规则观察") && observation.includes("根据午间保存事实整理") && !observation.includes("留存原文"));
assert.ok(observation.includes("成分股") && observation.includes("板块午后") && observation.includes('-0.97%'));
const manualObservation=renderToStaticMarkup(createElement(CloseObservation,{date:day,row:{label:"人工原关注",text:"保留原文"}}));
assert.ok(manualObservation.includes("记录时刻未提供") && !manualObservation.includes("原记录 2026"));
const fixture = { date: day, snapshot_id: "fixture-noon", state: "available", review: {
  review_id: "review-fixed", version: "noon-close-v1", revision: 1, generated_at_ms: 1793160000000,
  noon_collected_start_at: "2026-10-28T12:31:00+08:00", noon_collected_end_at: "2026-10-28T12:32:00+08:00",
  summary: { metrics: {}, indices: [], temperature: { noon: { score: 0 }, close: { score: null }, delta: null } },
  classification_counts: {}, observations: [], tomorrow: { groups: [] }, gaps: [],
} };
const render = value => {
  globalThis.__closeView = value; globalThis.__closePaths = [];
  globalThis.__closeItems = { review_id: "review-fixed", items: [row], total: 1, scope_total: 1 };
  return renderToStaticMarkup(createElement(CloseReview, { date: day, onStock(){} }));
};
const savedFetch = globalThis.fetch;
globalThis.fetch = () => { throw Error("static view cannot request providers"); };
try {
  const loaded = render(fixture);
  assert.ok(loaded.includes("合成核验股") && loaded.includes("收盘已核验"));
  assert.ok(loaded.includes("0.0"), "a real zero noon temperature remains visible");
  assert.ok(globalThis.__closePaths.some(p => p?.includes("review_id=review-fixed")));
  const keptVersion = render({ ...fixture, state: "partial", last_check_status: "warning", last_check_reason: "合成覆盖缺口，保留先前已核验版本", last_checked_at_ms:1793163600000 });
  assert.ok(keptVersion.includes('class="noon-close-check-warning"') && keptVersion.includes("本次覆盖不足，保留原核验版本") && keptVersion.includes("合成覆盖缺口"));
  assert.ok(keptVersion.includes("最近补核验") && keptVersion.includes("核验于"), "latest check time does not replace the saved version generation time");
  assert.ok(keptVersion.includes("合成核验股") && globalThis.__closePaths.some(p=>p?.includes("review_id=review-fixed")), "preserved review keeps the same version and rows");
  const stale = render({ ...fixture, date: "2026-10-27" });
  assert.ok(!stale.includes("合成核验股"), "a previous-day response must never flash on the selected date");
  assert.equal(globalThis.__closePaths.at(-1), null);
  for (const state of ["waiting_close", "not_collected", "no_snapshot", "purged"]) {
    const html = render({ date: day, state, review: null, snapshot_id: null });
    assert.ok(!html.includes("核验于") && !html.includes("午评采集 2026-"), state + " cannot invent timestamps");
    assert.ok(!html.includes("合成核验股"));
    assert.equal(globalThis.__closePaths.at(-1), null, state + " does not fetch detail rows");
  }
} finally {
  globalThis.fetch = savedFetch;
  delete globalThis.__closeView; delete globalThis.__closePaths; delete globalThis.__closeItems;
}
console.log("Real close-review UI: price/event colors, unknown/zero, date-revision boundary, loaded and four empty states passed");
