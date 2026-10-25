import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
async function load(path) {
  let compiled = ts.transpileModule(await fs.readFile(new URL(path, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
  if (compiled.includes('from "./chartDisplay"')) compiled = compiled.replace('"./chartDisplay"', JSON.stringify(await load("./chartDisplay.ts")));
  return "data:text/javascript;base64," + Buffer.from(compiled).toString("base64");
}
const { historicalChartEvents: collect, projectHistoricalChartEvent: project } = await import(await load("./historicalChartEvents.ts"));
const frozen = {
  type: "history_event", result: true,
  selected_event: { date: "2026-10-09", open: 9.5, high: 10.5, low: 9.4, close: 10, offset: 9, price_basis: "none" },
  event_checks: { result: true },
  evidence: { as_of: "2026-10-22", price_basis: "none" },
  history_event: { match: "latest", where: { all: [{ field: "volume_ratio", op: ">=", value: 3 }] }, price_path: { reclaim: { event_field: "high" } } },
};
const event = collect({ checks: [{ field: "unrelated" }, { checks: [frozen] }] })[0];
assert.equal(event.date, "2026-10-09"); assert.equal(event.rawClose, 10); assert.equal(event.rawPrice, 10.5); assert.equal(event.label, "倍量事件"); assert.equal(event.match, "最近一次");
assert.equal(collect({ ...frozen, history_event: { ...frozen.history_event, match: "earliest" } })[0].match, "最早一次");
const existential = { ...frozen, history_event: { ...frozen.history_event, match: "any" } };
assert.equal(collect(existential)[0].match, "最近一次已确认整体满足");
assert.equal(collect(existential)[0].date, frozen.selected_event.date, "any projects only the engine's frozen selected event, without reselecting");
for (const result of [false, null]) assert.deepEqual(collect({ ...existential, result }), [], "an unsuccessful/unknown any search must not show a fallback event marker");
assert.equal(collect({ ...frozen, result: null }).length, 1, "selected historical event remains visible when today's condition is unknown");
for (const value of [undefined, { ...frozen, selected_event: null }, { ...frozen, event_checks: { result: null } }, { ...frozen, selected_event: { ...frozen.selected_event, price_basis: "forward" } }, { ...frozen, selected_event: { ...frozen.selected_event, date: "2026-10-22" } }, { ...frozen, selected_event: { ...frozen.selected_event, date: "2026-02-30" } }]) assert.deepEqual(collect(value), []);
assert.equal(collect([frozen, { ...frozen, history_event: { match: "earliest", where: { field: "limit_up", op: "==", value: true } } }]).length, 2, "different rule references on the same date are not merged");
const fields = collect({ ...frozen, history_event: { ...frozen.history_event, then: { any: [{ field: "close", right_event_field: "low", right_event_change_pct: -3 }, { field: "close", right_event_field: "high" }] } } });
assert.deepEqual(fields.map(e => [e.priceField, e.rawPrice]), [["high", 10.5], ["low", 9.4]], "all actual event fields are referenced without applying today's percentage threshold");
assert.deepEqual(collect({ ...frozen, reference_fields: ["high", "low"] }).map(e => e.priceField), ["high", "low"], "compact API references and candidate definitions agree");
const raw = { as_of: "2026-10-22", period: "D", price_basis: "none", bars: [{ date: "2026-10-08", close: 9 }, { date: "2026-10-09", close: 10, high: 10.5 }, { date: "2026-10-22", close: 12 }] };
let p = project(event, raw); assert.equal(p.price, 10.5); assert.equal(p.closePrice, 10); assert.equal(p.slot, "2026-10-09");
assert.equal(project(event, { ...raw, bars: raw.bars.filter(b => b.date !== event.date) }).price, null, "never borrow a neighboring candle");
assert.equal(project(event, { ...raw, bars: raw.bars.map(b => ({ ...b, close: 11 })) }).price, null, "a different event raw close must fail closed");
assert.equal(project(event, { ...raw, bars: raw.bars.map(b => ({ ...b, high: 10.6 })) }).price, null, "non-close event prices require the exact raw field");
const adjusted = { ...raw, price_basis: "forward_event_affine", bars: raw.bars.map(b => ({ ...b, raw_close: b.close, raw_high: b.high, adjustment_anchor: raw.as_of })), adjustment: { usable: true, model: "event_affine", anchor: raw.as_of, quarantined: [], events_count: 1, events: [{ date: "2026-10-15", cash: 1, bonus: 1, rights: 0, rights_price: 0 }] } };
p = project(event, adjusted); assert.equal(p.price, 4.75); assert.equal(p.closePrice, 4.5); assert.equal(p.converted, true);
assert.equal(project(event, { ...adjusted, adjustment: { ...adjusted.adjustment, anchor: "2026-10-21" } }).price, null);
assert.equal(project(event, { ...raw, as_of: "2026-10-21" }).price, null, "a later rule binding must not appear as known on an earlier day");
const midweek = { ...event, date: "2026-10-07" };
assert.equal(project(midweek, { ...raw, period: "W", bars: [{ date: "2026-09-25" }, { date: "2026-10-09" }, { date: "2026-10-16" }] }).slot, "2026-10-09");
assert.equal(project(event, { ...raw, period: "M", bars: [{ date: "2026-09-30" }, { date: "2026-10-22" }] }).slot, "2026-10-22");
assert.equal(project(event, { ...raw, as_of: "2026-10-08" }).price, null, "no future event on an earlier observation day");
const { createStockNavigation, stockNavigationPosition, stepStockSelection } = await import(await load("./stockNavigation.ts"));
const candidate = { instrument_id: "600001.SH", checks: { checks: [structuredClone(frozen)] } };
const nav = createStockNavigation([candidate, { instrument_id: "600002.SH", checks: { checks: [] } }], { scopeKey: "run:matched", label: "frozen result", asOf: "2026-10-22" });
candidate.checks.checks[0].selected_event.date = "2026-10-01";
assert.equal(collect(nav.items[0].historicalChecks)[0].date, "2026-10-09", "navigation freezes selection evidence");
const stepped = stepStockSelection({ id: "600001.SH", navigation: nav }, 1);
assert.deepEqual(collect(stockNavigationPosition(stepped.navigation, stepped.id).current.historicalChecks), [], "next stock cannot inherit previous event");
console.log("Historical event chart references: frozen selection, selected day, reference field, adjustment, gaps and stock navigation passed");

const forwardSelected = structuredClone(frozen);
Object.assign(forwardSelected.selected_event, {
  price_basis: "forward_event_affine", anchor_date: "2026-10-22",
  close: 4.5, high: 4.75, low: 4.2, open: 4.25,
  raw_close: 10, raw_high: 10.5, raw_low: 9.4, raw_open: 9.5,
});
Object.assign(forwardSelected.evidence, { price_basis: "forward_event_affine", anchor_date: "2026-10-22" });
const forwardEvent = collect(forwardSelected)[0];
assert.equal(forwardEvent.rawClose, 10);
assert.equal(forwardEvent.rawPrice, 10.5);
assert.equal(forwardEvent.priceBasis, "none");
assert.equal(project(forwardEvent, adjusted).price, 4.75, "a frozen forward event is projected once from raw price, never adjusted twice");
assert.equal(project(forwardEvent, raw).price, 10.5, "raw chart uses the original reference rather than the frozen forward value");
for (const update of [{ anchor_date: "2026-10-21" }, { raw_close: null }, { raw_high: null }]) {
  const bad = structuredClone(forwardSelected); Object.assign(bad.selected_event, update);
  assert.deepEqual(collect(bad), [], "wrong anchors or missing original reference fields cannot silently reuse forward values as raw");
}
assert.deepEqual(collect({ ...forwardSelected, evidence: { ...forwardSelected.evidence, anchor_date: "2026-10-21" } }), []);
assert.equal(project(forwardEvent, { ...adjusted, adjustment: { ...adjusted.adjustment, anchor: "2026-10-23" } }).price, null, "a chart plan from another anchor is unknown");
const laterChart = { ...adjusted, as_of: "2026-10-23", bars: adjusted.bars.map((bar) => ({ ...bar, adjustment_anchor: "2026-10-23" })), adjustment: { ...adjusted.adjustment, anchor: "2026-10-23", events_count: 2, events: [...adjusted.adjustment.events, { date: "2026-10-23", cash: .2, bonus: 0, rights: 0, rights_price: 0 }] } };
assert.equal(project(forwardEvent, laterChart).price, 4.55, "with original prices retained, a later valid chart anchor can reproject using its own verified plan");
