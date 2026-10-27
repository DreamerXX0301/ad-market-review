import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, tsx = false) => ts.transpileModule(source, {
  fileName: tsx ? "component.tsx" : "helper.ts",
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
async function helper(url) {
  let code = compile(await fs.readFile(url, "utf8"));
  for (const match of [...code.matchAll(/from (["'])(\.[^"']+)\1/g)])
    code = code.replace(match[0], "from " + JSON.stringify(await helper(new URL(match[2] + ".ts", url))));
  return encode(code);
}
const react = JSON.stringify(import.meta.resolve("react"));
const panel = encode(`import {createElement as h} from ${react}; export const Badge=({children})=>h('span',null,children); export const Panel=({title,subtitle,children,className})=>h('section',{className},h('h2',null,title),h('p',null,subtitle),children);`);
const table = encode(`import {createElement as h} from ${react}; let saved; export const reset=()=>{saved=null}; export const current=()=>saved; export function SortableMarketTable(props){saved=props;return h('table',null,h('tbody',null,props.rows.map(row=>h('tr',{key:row.instrument_id},h('td',null,row.name)))));}`);
let code = compile(await fs.readFile(new URL("./LadderFeedback.tsx", import.meta.url), "utf8"), true);
code = code.replaceAll('from "react/jsx-runtime"', "from " + JSON.stringify(import.meta.resolve("react/jsx-runtime")))
  .replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const replacements = {
  "../../components/UI": panel,
  "./MarketTable": table,
  "./BoardTags": encode("export default function BoardTags(){return null}"),
  "../../lib/format": await helper(new URL("../../lib/format.ts", import.meta.url)),
  "../../components/stockNavigation": await helper(new URL("../../components/stockNavigation.ts", import.meta.url)),
  "./ladderFeedbackSummary": await helper(new URL("./ladderFeedbackSummary.ts", import.meta.url)),
  "./boardPresentation": await helper(new URL("./boardPresentation.ts", import.meta.url)),
};
for (const [path, url] of Object.entries(replacements)) code = code.replaceAll('from "' + path + '"', "from " + JSON.stringify(url));
const {default: Feedback, LadderFeedbackOverview} = await import(encode(code));
const captures = await import(table);
const day = "2026-10-23", previous = "2026-10-22", snapshot = "synthetic-layout-only";
function row(symbol, height, change) {
  return {instrument_id:symbol, name:symbol, date:day, snapshot_id:snapshot, change_pct:change, previous_board_date:previous, previous_board_count:height,
    board_presentation:{version:"dated-stock-board-presentation-v1", date:day, snapshot_id:snapshot, instrument_id:symbol, previous_height:{state:"confirmed",value:height,source_date:previous}}};
}
const details = [row("600001.SH", 2, 10), row("600002.SH", 3, -5)];
const first = [row("600003.SH", 1, 3), row("600004.SH", 1, null)];
const props = {rows:details, firstBoardRows:first, firstBoardCoverage:{state:"ready",confirmed_count:2,unverified_count:0}, date:day, snapshotId:snapshot};
const navigation = [];
const html = renderToStaticMarkup(createElement(Feedback,{...props,onStock:(...args)=>navigation.push(args)}));
assert.deepEqual(captures.current().rows.map(row=>row.instrument_id), details.map(row=>row.instrument_id), "first boards cannot leak into the detail table or its stock navigation");
assert.deepEqual(captures.current().defaultSort, {key:"feedback_height",order:"desc"});
assert.ok(html.includes("<strong>首板</strong>"), "the height summary has an actual first-board group");
assert.ok(html.includes("+2.67%"), "all returned overview samples, including first boards, determine the mean");
assert.ok(html.includes("明细仅昨日二板及以上"));
captures.current().onStockNavigate(details[0].instrument_id, details);
assert.equal(navigation[0][1].asOf, day);
assert.ok(navigation[0][1].scopeKey.startsWith("emotion:feedback:"));
captures.reset();
const compact = renderToStaticMarkup(createElement(LadderFeedbackOverview,{...props,compact:true}));
assert.equal(captures.current(), null, "overview summary must not render the full detail table again");
assert.ok(compact.includes('href="#emotion"'));
assert.ok(compact.includes("ladder-feedback-overview--compact"));
assert.ok(compact.includes("<strong>首板</strong>"));
const missing = renderToStaticMarkup(createElement(LadderFeedbackOverview,{...props,firstBoardRows:[],firstBoardCoverage:{state:"unknown",confirmed_count:0,unverified_count:0}}));
assert.ok(missing.includes("待核验"));
assert.ok(!missing.includes("已留存0只"), "missing first-board source is not a known empty sample");
console.log("Feedback layout: first-board summary only, separate multi-board detail, complete-sample mean, dated navigation, compact overview and unknown coverage passed");
