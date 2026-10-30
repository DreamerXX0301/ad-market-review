import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./noonWorkbenchPresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { noonConfirmedHeight, noonFeedbackRows, noonFeedbackPage, noonFeedbackState, noonDirectionState, noonLadderRows } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));

for (const unknown of [null, undefined, false, true, "2", 0, -1, 1.5, NaN, Infinity]) {
  assert.equal(noonConfirmedHeight(unknown), null);
}
assert.equal(noonConfirmedHeight(2), 2);
const rows = [
  { instrument_id: "600001.SH", previous_board_count: 1, change_pct: 0 },
  { instrument_id: "600003.SH", previous_board_count: 3, change_pct: -2 },
  { instrument_id: "600002.SH", previous_board_count: 3, change_pct: null },
  { instrument_id: "600004.SH", previous_board_count: null, change_pct: 4 },
  { instrument_id: "600005.SH", previous_board_count: 0, change_pct: 1 },
];
const original = structuredClone(rows);
assert.deepEqual(noonFeedbackRows(rows).map((r) => r.instrument_id), ["600002.SH", "600003.SH", "600001.SH", "600004.SH", "600005.SH"]);
assert.deepEqual(noonFeedbackRows(rows, "first").map((r) => r.instrument_id), ["600001.SH"]);
assert.deepEqual(noonFeedbackRows(rows, "multi").map((r) => r.instrument_id), ["600002.SH", "600003.SH"]);
assert.deepEqual(noonFeedbackRows(rows, "unknown").map((r) => r.instrument_id), ["600004.SH", "600005.SH"]);
assert.deepEqual(rows, original, "filters and ordering cannot alter frozen items or server aggregates");

const many = Array.from({ length: 42 }, (_, i) => ({ instrument_id: String(i), change_pct: i % 3 === 0 ? null : i }));
const allPages = Array.from({ length: 4 }, (_, p) => noonFeedbackPage(many, p).rows).flat();
assert.deepEqual(allPages, many, "every retained sample must remain reachable beyond the initial rows");
assert.equal(noonFeedbackPage(many, 99).page, 3);
assert.equal(noonFeedbackPage(many, -10).page, 0);
assert.deepEqual(noonFeedbackPage([], 9), { rows: [], page: 0, pages: 1, total: 0, start: 0, end: 0 });
assert.equal(noonFeedbackPage(many, 0, 0).rows.length, 12);
assert.deepEqual(noonLadderRows([
  { instrument_id: "3", continue_day_cnt: null },
  { instrument_id: "2", continue_day_cnt: 2 },
  { instrument_id: "1", continue_day_cnt: 5 },
]).map((row) => row.instrument_id), ["1", "2", "3"]);

assert.equal(noonFeedbackState("limit_up"), "本次涨停池");
assert.equal(noonFeedbackState("limit_break"), "本次炸板池");
assert.equal(noonFeedbackState("limit_down"), "本次跌停池");
assert.equal(noonFeedbackState("not_listed"), "本次三池未列入");
assert.equal(noonFeedbackState("conflict"), "池状态冲突");
assert.equal(noonFeedbackState(null), "池状态未知");
assert.equal(noonFeedbackState("promoted"), "池状态未知", "unrecognized states do not become a final promotion");
assert.equal(noonDirectionState("turned_weak"), "前日涨 · 午间跌");
assert.equal(noonDirectionState(undefined), "跨日表现未知");
console.log("noon workbench: strict heights, full-sample pagination, immutable ordering, intraday labels passed");

// Render the actual component without a browser: old or partial snapshots must
// remain readable and full-group statistics must survive detail pagination.
const { build } = await import("vite");
const { createRequire } = await import("node:module");
const { pathToFileURL } = await import("node:url");
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const require = createRequire(import.meta.url);
const result = await build({
  configFile: false,
  root: new URL("../../../", import.meta.url).pathname,
  logLevel: "silent",
  build: { write: false, ssr: new URL("./NoonWorkbenchAnalysis.tsx", import.meta.url).pathname, minify: false },
});
const output = result.output.find((item) => item.type === "chunk" && item.isEntry);
const code = output.code.replace(/from "([^".][^"]*)"/g, (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`);
const { default: Workbench } = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
const missing = renderToStaticMarkup(createElement(Workbench, { workbench: null }));
assert.ok(missing.includes("该快照尚未提供可核验的跨日分析"));
const unknownGroup = renderToStaticMarkup(createElement(Workbench, { workbench: {
  version: "noon-workbench-v1", previous_feedback: { groups: [{ label: "昨日首板", count: null, valid_count: 0, pool_counts: { limit_up: 0 } }] },
} }));
assert.ok(unknownGroup.includes("分组表现保持未知"));
assert.ok(!unknownGroup.includes("noon-feedback-pools"), "unknown source groups must not display zero pool events");
const samples = many.map((row, i) => ({ ...row, name: "合成测试样本" + i, previous_board_count: 2, latest_price: 10, current_state: "unknown" }));
const aggregate = {
  key: "board_2", label: "昨日二板", count: 42, valid_count: 28,
  unknown_count: 14, up: 10, down: 18, flat: 0,
  mean_pct: 0.75, median_pct: -0.5, large_drop_count: 3,
  pool_counts: { limit_up: 0, limit_break: 0, limit_down: 0, unknown: 41, conflict: 1 }, items: samples,
};
let fetches = 0;
const priorFetch = globalThis.fetch;
globalThis.fetch = async () => { fetches += 1; throw new Error("snapshot rendering must not fetch"); };
try {
  const html = renderToStaticMarkup(createElement(Workbench, {
    chartDate: "2026-10-27", onStock() {},
    workbench: {
      version: "noon-workbench-v1",
      previous_feedback: { date: "2026-10-27", source_count: 45, verified_count: 42, valid_count: 28, unknown_count: 14,
        max_confirmed_height: 2, highest_items: [samples[0]],
        groups: [aggregate, { key: "height_unknown", label: "高度未核验样本", count: 0 }], items: samples },
      negative_feedback: { cohorts: [{ key: "limit_break", label: "昨日炸板样本反馈", state: "unknown", verified_count: null, reason: "昨日事件样本未留存" }] },
      today_ladder: { items: [], distribution: [], unknown_count: null },
      directions: { comparison: Array.from({ length: 710 }, (_, i) => ({ instrument_id: `fixture-sector-${i}`, name: `合成板块${i}`, kind: "concept", previous_change_pct: 1, change_pct: i ? 1 : null, state: i ? "both_up" : "unknown" })), current_leaders: [], current_laggards: [], watched: [] },
      afternoon: [{ kind: "test", object: "fixture", label: "合成测试观察", evidence: "合成当前证据", condition: "合成待验证条件" }],
    },
  }));
  assert.ok(html.includes("池状态未知 41") && html.includes("池状态冲突 1"), "pool coverage gaps must be visible separately from 14 missing quotes");
  assert.ok(html.includes("昨日已核验样本最高") && html.includes("2板"));
  assert.ok(!html.includes("高度未核验样本"), "a zero unknown-height cohort must not imply that yesterday's maximum is unknown");
  assert.ok(html.includes("+0.75%"), "display complete group mean supplied by backend");
  assert.ok(html.includes("共 42 只"));
  assert.ok(html.includes("合成测试样本41"), "height cards must show the complete group's names directly, beyond the detail page");
  assert.ok(html.includes("1 / 4"), "all 42 samples are reachable through four pages");
  assert.ok(html.includes("昨日事件样本未留存"));
  assert.ok(html.includes("板块跨日对照<span>710</span>"));
  assert.ok(!html.includes("合成板块12"), "inactive comparison tab does not render its 710 rows");
  assert.ok(html.includes("合成当前证据") && html.includes("合成待验证条件"));
  assert.ok(html.indexOf("昨日接力反馈") < html.indexOf("今日连板梯队"));
  assert.ok(html.indexOf("今日连板梯队") < html.indexOf("主线与板块对照"));
  assert.ok(html.indexOf("主线与板块对照") < html.indexOf("午后观察清单"));
  const four = [11, 12, 13].map((i) => ({ instrument_id: `6000${i}.SH`, name: `合成四板股${i}`, previous_board_count: 4, change_pct: i - 12 }));
  Object.assign(four[0], { theme_text: "合成昨日题材一+合成昨日题材二+合成昨日题材三", theme_source: "previous_session_pool", theme_date: "2026-10-27" });
  Object.assign(four[1], { theme_text: "合成午间题材", theme_source: "current_noon_pool", theme_date: "2026-10-28" });
  const five = [{ instrument_id: "600014.SH", name: "合成五板股", previous_board_count: 5, change_pct: null }];
  const fivePlus = [{ instrument_id: "600015.SH", name: "合成六板股", previous_board_count: 6, change_pct: 2 }, ...five];
  Object.assign(fivePlus[0], { theme_text: "合成人工题材", theme_source: "previous_manual", theme_date: "2026-10-27" });
  const todayFirst = Array.from({ length: 71 }, (_, i) => ({ instrument_id: String(610000 + i) + ".SH", name: `合成今日首板${i}`, continue_day_cnt: 1, change_pct: 10 }));
  todayFirst[0].limit_up_reason = "合成冻结题材原因+合成线索二+合成线索三+合成线索四+合成线索五";
  const heights = renderToStaticMarkup(createElement(Workbench, {
    chartDate: "2026-10-27", onStock() {}, onSector() {},
    workbench: {
      version: "noon-workbench-v1",
      previous_feedback: { date: "2026-10-27", max_confirmed_height: 6, highest_items: [fivePlus[0]], groups: [
        { ...aggregate, key: "first", board_count: 1, label: "昨日首板", count: 0, items: [] },
        { ...aggregate, key: "board_2", board_count: 2, label: "昨日二板", count: 0, items: [] },
        { ...aggregate, key: "board_3", board_count: 3, label: "昨日三板", count: 0, items: [] },
        { ...aggregate, key: "board_4", board_count: 4, label: "昨日四板", count: 3, items: four },
        { ...aggregate, key: "board_5_plus", board_count: null, label: "昨日五板及以上", count: 2, items: fivePlus },
      ] },
      today_ladder: { state: "observed", distribution: [{ board_count: 1, count: 71, items: todayFirst }], items: todayFirst },
      directions: { current_leaders: [
        { instrument_id: "881001.TI", name: "合成可点行业", kind: "industry", change_pct: 1, member_limit_up_count: 0, member_limit_up_state: "observed", member_limit_up_reason: "合成完整覆盖" },
        { instrument_id: "881002.TI", name: "合成部分覆盖", kind: "industry", change_pct: 1, member_limit_up_count: 3, member_limit_up_state: "partial", member_limit_up_reason: "合成样本至少三只，完整范围未知" },
        { instrument_id: "881003.TI", name: "合成未知覆盖", kind: "industry", change_pct: 1, member_limit_up_count: null, member_limit_up_state: "unknown", member_limit_up_reason: "合成未取得成员名单" },
      ] },
    },
  }));
  const fourNames = heights.match(/aria-label="昨日四板对应股票">([\s\S]*?)<\/ul>/)?.[1] || "";
  const fiveNames = heights.match(/aria-label="昨日五板及以上对应股票">([\s\S]*?)<\/ul>/)?.[1] || "";
  for (const item of four) assert.ok(fourNames.includes(item.name), "each four-board stock is directly in its own height card");
  assert.ok(!fourNames.includes("合成五板股") && !fiveNames.includes("合成四板股"), "four and five boards must not mix their members");
  assert.ok(fiveNames.includes("合成五板股") && fourNames.includes("<button"), "height member names retain the stock chart action");
  const visibleFourNames = fourNames.replace(/<details[\s\S]*?<\/details>/g, "");
  for (const item of four) assert.ok(visibleFourNames.includes(item.name), "stock names remain visible independently of expandable source reasons");
  assert.ok(fourNames.includes("合成昨日题材一") && fourNames.includes("+1") && fourNames.includes(four[0].theme_text), "prior clue tags preserve the full frozen source text");
  assert.ok(fourNames.includes("午间来源") && fourNames.includes("不是昨日归因") && fourNames.includes("昨日来源"));
  assert.ok(fourNames.includes("行业/概念未留存") && fiveNames.includes("人工判断"), "missing memberships and manual claims stay distinct");
  assert.ok(!heights.includes("四板及以上"));
  assert.equal((heights.match(/class="noon-feedback-group"/g) || []).length, 5, "the main review keeps five fixed height cards");
  assert.ok(fiveNames.includes("昨日5板") && fiveNames.includes("昨日6板"), "five-and-above members retain their actual prior heights");
  assert.ok(fiveNames.indexOf("合成六板股") < fiveNames.indexOf("合成五板股"));
  assert.ok(heights.indexOf("<h3>昨日五板及以上") < heights.indexOf("<h3>昨日四板") && heights.indexOf("<h3>昨日四板") < heights.indexOf("<h3>昨日首板"), "height groups run from high to low");
  assert.ok(fourNames.indexOf("合成四板股13") < fourNames.indexOf("合成四板股12"), "same-height names default to percentage descending");
  assert.ok(heights.includes("合成今日首板70"), "all current ladder names render directly rather than stopping after eight");
  assert.ok(!heights.includes("只见下方完整样本"));
  assert.ok(heights.includes("合成冻结题材原因") && heights.includes("行业/概念未留存"), "current ladder displays its retained source reason or an explicit gap");
  const todayTable = heights.match(/<table class="noon-ladder-table"[\s\S]*?<\/table>/)?.[0] || "";
  assert.ok(todayTable.includes("股票") && todayTable.includes("午间涨幅") && todayTable.includes("行业 / 概念与原因"), "current ladder uses aligned stock, change and clue columns");
  const firstTables = [...heights.matchAll(/<table class="noon-ladder-table"[\s\S]*?<\/table>/g)].map(match => match[0]);
  assert.equal(firstTables.length, 2, "wide first-board layout uses two complete native tables, never a grid tbody");
  assert.equal(firstTables.reduce((sum, table) => sum + (table.match(/<tr>/g) || []).length - 1, 0), 71, "all 71 stocks remain in the two complete bounded tables");
  assert.ok(todayTable.includes('class="noon-ladder-clue"') && todayTable.includes("+2"));
  assert.ok(todayTable.includes("本次来源原文") && todayTable.includes(todayFirst[0].limit_up_reason), "extra clues can expand to the exact retained reason");
  assert.ok(!todayTable.includes("题材/原因："), "clue tags replace repetitive long reason prefixes");
  for (const id of ["noon-prior-feedback", "noon-today-ladder", "noon-directions", "noon-afternoon"]) assert.ok(heights.includes(`id="${id}"`));
  assert.ok(heights.includes("不代表已核验归因"));
  assert.match(heights, /<button[^>]*class="noon-sector-link"[^>]*>合成可点行业<\/button>/, "sector names expose the provided detail callback as a real button");
  assert.ok(heights.includes("查看合成可点行业成分股") && heights.includes("板块K线"), "member and chart actions are distinct");
  const sectorTable = heights.match(/aria-label="本次领涨方向">([\s\S]*?)<\/table>/)?.[1] || "";
  assert.ok(sectorTable.includes("样本涨停"));
  assert.deepEqual([...sectorTable.matchAll(/class="noon-number-column noon-sector-limit-up "[^>]*>(.*?)<\/td>/g)].map((match) => match[1]), ["0", "≥3", "—"], "confirmed zero, partial lower bound and unknown must remain distinct");
  assert.ok(sectorTable.includes("完整范围未知") && sectorTable.includes("未取得成员名单"), "coverage reasons remain attached to each count");
  assert.equal(fetches, 0);
  const classified = {instrument_id:"600001.SH",name:"合成已归属股票",previous_board_count:4,change_pct:-5.18,
    industries:[{topic_code:"881001.TI",name:"合成行业",kind:"industry",members_observed_date:"2026-10-21",source:"fuyao"}],
    topics:[{topic_code:"885001.TI",name:"合成概念",kind:"concept",members_observed_date:"2026-10-21",source:"fuyao"}],membership_state:"observed_sample"};
  const memberships = renderToStaticMarkup(createElement(Workbench,{workbench:{version:"noon-workbench-v1",previous_feedback:{groups:[{...aggregate,label:"昨日四板",items:[classified]}],items:[classified]},negative_feedback:{high_position_drops:[classified]}}}));
  assert.ok(memberships.includes("合成行业") && memberships.includes("合成概念"));
  assert.ok(!memberships.includes("行业/概念未留存"), "missing limit-up cause must not hide available industry/concept memberships");
  assert.match(memberships, /<details class="noon-details" open=""><summary>昨日高位样本明显回落/, "high-position feedback is open by default");
  assert.ok(memberships.includes("成员名单日 2026-10-21") && memberships.includes("行业 / 概念"), "high-position feedback also carries dated membership tags");
  const boardRows = [
    {instrument_id:"600001.SH",name:"板别测试主板",previous_board_count:1,continue_day_cnt:1,board_count:1,change_pct:10},
    {instrument_id:"300001.SZ",name:"板别测试创业",previous_board_count:1,continue_day_cnt:1,board_count:1,change_pct:20},
    {instrument_id:"688001.SH",name:"板别测试科创",previous_board_count:1,continue_day_cnt:1,board_count:1,change_pct:20},
  ];
  const boardModel = {version:"noon-workbench-v1",previous_feedback:{items:boardRows,groups:[{...aggregate,count:3,label:"昨日首板",board_count:1,items:boardRows}]},today_ladder:{state:"observed",items:boardRows,distribution:[{board_count:1,count:3,items:boardRows}]}};
  const boardBefore=JSON.stringify(boardModel);
  const filteredBoard=renderToStaticMarkup(createElement(Workbench,{workbench:boardModel,boardFilter:"chinext",onStock(){},chartDate:"2026-10-27"}));
  assert.ok(filteredBoard.includes("板别测试创业") && !filteredBoard.includes("板别测试主板") && !filteredBoard.includes("板别测试科创"));
  assert.ok(filteredBoard.includes("创业板") && filteredBoard.includes("+0.75%"), "board filtering changes lists, never the server's aggregate mean");
  assert.ok(filteredBoard.includes("当前板别显示1只") && filteredBoard.includes("1 / 3只"));
  assert.ok(renderToStaticMarkup(createElement(Workbench,{workbench:boardModel,boardFilter:"beijing"})).includes("该板别暂无展示样本"));
  assert.equal(JSON.stringify(boardModel),boardBefore);
} finally {
  globalThis.fetch = priorFetch;
}
console.log("noon workbench render: frozen aggregates, full detail pages, missing history and concrete observations passed");
