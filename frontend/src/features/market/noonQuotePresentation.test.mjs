import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(new URL("./noonQuotePresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { noonSortRows, noonKnownHeight, noonQuoteContext, noonEnrichQuotes, noonEventLabels } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const values = [{ id: "ten", value: 10 }, { id: "nil", value: null },
  { id: "zero", value: 0 }, { id: "two", value: 2 }, { id: "blank", value: "" },
  { id: "nan", value: NaN }, { id: "decimal", value: "2.5" }, { id: "tie", value: 2 }];
const before = values.slice();
assert.deepEqual(noonSortRows(values, "value", "desc").map((row) => row.id),
  ["ten", "decimal", "two", "tie", "zero", "nil", "blank", "nan"]);
assert.deepEqual(noonSortRows(values, "value", "asc").map((row) => row.id),
  ["zero", "two", "tie", "decimal", "ten", "nil", "blank", "nan"]);
assert.deepEqual(values, before, "sorting never changes the frozen row order");
assert.deepEqual(noonSortRows([{name:"2026-10-28"},{name:"2026-09-30"}], "name", "asc").map((row) => row.name),
  ["2026-09-30", "2026-10-28"]);
assert.equal(noonKnownHeight(0), null);
assert.equal(noonKnownHeight("5"), null);
assert.equal(noonKnownHeight(true), null);
assert.equal(noonKnownHeight(5), 5);
const industry = {name:"合成行业",kind:"industry",members_observed_date:"2026-10-21"};
const topic = {name:"合成概念",kind:"concept",members_observed_date:"2026-10-21"};
const snapshot = {
  membership_context:{stock_classifications:{"600001.SH":{industries:[industry],topics:[topic]}}},
  workbench:{previous_feedback:{items:[{instrument_id:"600001.SH",previous_board_count:5}]},
    today_ladder:{distribution:[{items:[{instrument_id:"600002.SH",continue_day_cnt:9}]}]},
    directions:{watched:[{items:[{instrument_id:"600003.SH",previous_board_count:2,theme_text:"昨日主题",theme_date:"2026-10-27",theme_source:"previous_manual"}]}]}},
};
const original = JSON.stringify(snapshot);
const context = noonQuoteContext(snapshot);
assert.equal(context["600001.SH"].previous_board_count, 5);
assert.deepEqual(context["600001.SH"].industries, [industry]);
assert.deepEqual(context["600001.SH"].topics, [topic]);
assert.equal(context["600002.SH"].previous_board_count, undefined, "today's height never supplies yesterday");
assert.equal(context["600003.SH"].previous_board_count, 2);
assert.equal(context["600003.SH"].theme_text, "昨日主题");
const enriched = noonEnrichQuotes([{instrument_id:"600001.SH",latest_price:10}], context);
assert.equal(enriched[0].latest_price, 10);
assert.equal(enriched[0].previous_board_count, 5);
assert.equal(noonEnrichQuotes([{instrument_id:"600001.SH",previous_board_count:null}],context)[0].previous_board_count, 5);
assert.equal(noonEnrichQuotes([{instrument_id:"600009.SH",previous_board_count:9}],context)[0].previous_board_count, null,
  "current provider fields cannot introduce an unverified yesterday height");
assert.equal(JSON.stringify(snapshot), original, "enrichment is a local view of returned evidence");
console.log("noon quote presentation: stable numeric sorting, null-last, dated classifications and verified previous heights passed");

const eventSnapshot = {payload:{previous_review:{date:"2026-10-27"},events:{limit_up:{state:"observed",items:[{instrument_id:"600001.SH",continue_day_cnt:3},{instrument_id:"600002.SH",continue_day_cnt:1}]},limit_down:{state:"observed",items:[{instrument_id:"600003.SH"}]},limit_break:{state:"partial",items:[{instrument_id:"600001.SH"}]}}},workbench:{negative_feedback:{cohorts:[{key:"limit_down",date:"2026-10-27",state:"partial",items:[{instrument_id:"600002.SH"}]},{key:"limit_break",date:"2026-10-26",state:"observed",items:[{instrument_id:"600003.SH"}]}]}}};
const eventBefore=JSON.stringify(eventSnapshot), eventContext=noonQuoteContext(eventSnapshot);
assert.deepEqual(noonEventLabels(eventContext["600002.SH"]),[{label:"本次首板",tone:"up"},{label:"昨日跌停",tone:"down"}]);
assert.deepEqual(noonEventLabels(eventContext["600003.SH"]),[{label:"本次跌停",tone:"down"}]);
assert.deepEqual(noonEventLabels(eventContext["600001.SH"]),[{label:"本次池状态冲突",tone:"neutral"}], "conflicting pools never assert a current promotion");
assert.deepEqual(noonEventLabels({current_event_state:"limit_up",current_board_count:5,previous_board_count:4}),[{label:"本次5连板",tone:"up"},{label:"昨日4连板",tone:"up"}]);
assert.deepEqual(noonEventLabels({current_event_state:"unknown",previous_board_count:null}),[]);
assert.equal(JSON.stringify(eventSnapshot),eventBefore);
