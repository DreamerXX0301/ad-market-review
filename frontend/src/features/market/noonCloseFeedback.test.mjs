import assert from "node:assert/strict";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const output = await build({ configFile: false, root: new URL("../../../", import.meta.url).pathname, logLevel: "silent",
  build: { write: false, ssr: new URL("./NoonWorkbenchAnalysis.tsx", import.meta.url).pathname, minify: false } });
const entry = output.output.find(item => item.type === "chunk" && item.isEntry);
const require = createRequire(import.meta.url);
const code = entry.code.replace(/from "([^".][^"]*)"/g, (_, id) => "from " + JSON.stringify(pathToFileURL(require.resolve(id)).href));
const { default: Workbench } = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
const base = { version: "noon-workbench-v1", previous_feedback: {}, today_ladder: {}, directions: {}, afternoon: [] };
const render = feedback => renderToStaticMarkup(createElement(Workbench, { workbench: { ...base, previous_close_feedback: feedback }, chartDate: "2026-10-28", onStock(){} }));
assert.ok(!render({ state: "missing", groups: [] }).includes("昨日午后变化反馈"), "absent transfer lists do not create empty panels");
assert.ok(!render({ groups: [{ key: "opened", source_count: 0 }] }).includes("昨日午后变化反馈"));
const html = render({ date: "2026-10-28", state: "available", groups: [{
  key: "resealed", label: "昨日午间炸板、收盘回封", source_count: 1, valid_count: 1, unknown_count: 0,
  up: 0, down: 0, flat: 1, mean_pct: 0,
  items: [{ instrument_id: "000001.SZ", name: "合成昨日回封", previous_board_count: 3, latest_price: 11, change_pct: 0,
    current_state: "not_listed", industries: [{ name: "合成行业" }], topics: [{ name: "合成概念" }] }],
}] });
assert.ok(html.includes("昨日午后变化反馈") && html.includes("昨日午间炸板、收盘回封"));
assert.ok(html.includes("合成昨日回封") && html.includes("合成行业") && html.includes("合成概念"));
assert.ok(html.includes("3板"), "yesterday's actual closing height is retained");
assert.ok(html.includes("0.00%"), "known flat feedback is not hidden");
assert.ok(html.includes('class="noon-details" open=""'), "attached transfer rows open by default");
assert.ok(html.includes("附加名单可能重叠，不计入原五组家数或温度"));
const eventRows = ["limit_up", "limit_break", "limit_down", "conflict", "not_listed"].map(
  (state, index) => ({
    instrument_id: `60000${index}.SH`, name: state, latest_price: 11,
    change_pct: 9.99, current_state: state,
  }),
);
const eventHtml = render({ groups: [{ key: "new_limit_up", source_count: 5, items: eventRows }] });
assert.equal((eventHtml.match(/本次午间来源池已核验/g) || []).length, 3);
for (const event of ["涨停", "跌停", "炸板"])
  assert.ok(eventHtml.includes(`本次午间来源池已核验${event}，收盘状态另行核验`));
console.log("Next-session closing feedback: no empty panel, frozen prior heights/classifications, zero return, default-open rows and independent totals passed");
