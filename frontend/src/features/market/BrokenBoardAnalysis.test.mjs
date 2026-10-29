/** Actual component rendered from dated synthetic cohorts; no real API requests. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (s) =>
  "data:text/javascript;base64," + Buffer.from(s).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime"),
  react = import.meta.resolve("react");
const hooks = encode(
  `import * as React from ${JSON.stringify(react)};export * from ${JSON.stringify(react)};let cursor=0,states={};export function reset(value={}){cursor=0;states=value;}export function useState(initial){const i=cursor++;return React.useState(Object.hasOwn(states,i)?states[i]:initial);}`,
);
const api = encode(
  `export const requests=[];let response=null;export function setResponse(value){response=value;requests.length=0;}export function useApi(path){requests.push(path);return{data:response,error:'',loading:false,reload(){}};}`,
);
const ui = encode(
  `import{jsx,jsxs}from ${JSON.stringify(runtime)};export const Badge=({children})=>jsx('span',{children});export const Panel=({children,title})=>jsxs('section',{children:[jsx('h2',{children:title}),children]});export const Empty=({title})=>jsx('p',{children:title});export const ErrorBox=({message})=>jsx('p',{children:message});export const Loading=()=>jsx('p',{children:'loading'});`,
);
const table = encode(
  `import{jsx,jsxs}from ${JSON.stringify(runtime)};export const seen=[];export function SortableMarketTable(props){seen.push(props);return jsxs('table',{children:[jsx('thead',{children:jsx('tr',{children:props.columns.map(c=>jsx('th',{children:c.title},c.key))})}),jsx('tbody',{children:props.rows.map((r,i)=>jsx('tr',{children:props.columns.map(c=>jsx('td',{children:c.render?c.render(r[c.key],r,i):String(r[c.key]??'—')},c.key))},r.instrument_id))})]});}`,
);
const replacements = {
  react: hooks,
  "../../lib/api": api,
  "../../components/UI": ui,
  "./MarketTable": table,
  "./MetricHelp": encode(`export const MetricHelp=()=>null;`),
  "./MarketMemberMetrics": encode(
    `import{jsx}from ${JSON.stringify(runtime)};export const MemberBoardCell=({row})=>jsx('span',{children:row.board_label||row.board});`,
  ),
};
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let code = ts
      .transpileModule(await fs.readFile(url, "utf8"), {
        fileName: url.pathname,
        compilerOptions: {
          module: ts.ModuleKind.ES2022,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      })
      .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
    for (const name of [
      ...new Set(
        [...code.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]),
      ),
    ]) {
      let target = replacements[name];
      if (!target && name.startsWith(".")) {
        for (const ext of [".ts", ".tsx"]) {
          const next = new URL(name + ext, url);
          try {
            await fs.access(next);
            target = await moduleUrl(next);
            break;
          } catch (e) {
            if (e.code !== "ENOENT") throw e;
          }
        }
      }
      target ||= import.meta.resolve(name);
      code = code
        .replaceAll('from "' + name + '"', "from " + JSON.stringify(target))
        .replaceAll("from '" + name + "'", "from " + JSON.stringify(target));
    }
    return encode(code);
  })();
  cache.set(url.href, job);
  return job;
}
const { default: Component } = await import(
  await moduleUrl(new URL("./BrokenBoardAnalysis.tsx", import.meta.url))
);
const { reset } = await import(hooks),
  { seen } = await import(table),
  { requests, setResponse } = await import(api);
const day = "2026-10-27",
  previous = "2026-10-26",
  snapshot = "synthetic-generation";
const row = (id, height, change, state = "limit_up", board = "main") => ({
  instrument_id: id,
  name: "合成" + id,
  code: id.slice(0, 6),
  date: day,
  snapshot_id: snapshot,
  outcome: "broken",
  board,
  board_label: board,
  change_pct: change,
  drawdown_from_high_pct: -3,
  previous_event_date: previous,
  previous_event_version: "broken-board-prior-events-v1",
  previous_event_source: "fuyao",
  previous_state: state,
  previous_board_count: height,
});
const items = [
  row("600001.SH", 2, -1),
  row("688001.SH", 2, 4, "limit_up", "star"),
  row("600002.SH", 8, 0),
  row("600003.SH", null, 3, "limit_break"),
  row("600004.SH", null, -2, "none"),
];
const data = {
  version: "broken-board-analysis-v1",
  date: day,
  snapshot_id: snapshot,
  current: {
    date: day,
    snapshot_id: snapshot,
    state: "complete",
    limit_up_count: 0,
    broken_count: 5,
    touch_count: 5,
    broken_rate_pct: 100,
    previous_event_date: previous,
    items,
  },
  history: [],
  previous_feedback: {
    date: day,
    previous_date: previous,
    snapshot_id: snapshot,
    state: "partial",
    items: [
      {
        ...row("600003.SH", null, 3, "limit_break"),
        current_state: "not_limit_up",
      },
    ],
  },
};
const scope = JSON.stringify([day, snapshot]),
  filterScope = JSON.stringify([scope, day, "current"]);
function render(states = {}, value = data, date = day, response = null) {
  seen.length = 0;
  reset(states);
  setResponse(response);
  const html = renderToStaticMarkup(
    createElement(Component, {
      data: value,
      date,
      snapshotId: snapshot,
      onStock() {},
    }),
  ).replaceAll(/<!--[^]*?-->/g, "");
  return { html, table: seen.at(-1) };
}
let result = render();
assert.deepEqual(
  result.table.rows.map((r) => r.instrument_id),
  ["600002.SH", "688001.SH", "600001.SH", "600003.SH", "600004.SH"],
);
assert.deepEqual(result.table.defaultSort, {
  key: "previous_board_count",
  order: "desc",
});
assert.match(result.html, /昨日8板/);
assert.match(result.html, /昨日炸板/);
assert.match(result.html, /筛选昨日板型/);
assert.match(result.html, /今日涨幅/);
assert.match(result.html, /收盘距当日高点/);
assert.ok(
  requests.every((path) => path === null),
  "present observation uses its original report",
);
result = render({
  3: { scope: filterScope, value: "star" },
  4: { scope: filterScope, value: "2" },
});
assert.deepEqual(
  result.table.rows.map((r) => r.instrument_id),
  ["688001.SH"],
  "board + previous-height filters intersect",
);
assert.match(result.html, /明细筛选后 1 \/ 5 只/);
assert.match(
  result.html,
  /昨日五板及以上 <b>1<\/b>/,
  "queue-wide quick counts retain the complete original cohort",
);
result = render({ 4: { scope: filterScope, value: "broken" } });
assert.deepEqual(
  result.table.rows.map((r) => r.instrument_id),
  ["600003.SH"],
);
result = render({ 4: { scope: filterScope, value: "unmarked" } });
assert.deepEqual(
  result.table.rows.map((r) => r.instrument_id),
  ["600004.SH"],
);
assert.doesNotMatch(
  result.html,
  /<td>.*昨日首板/s,
  "no-event row is never a first board",
);
result = render({ 0: "previous" });
assert.deepEqual(
  result.table.rows.map((r) => r.instrument_id),
  ["600003.SH"],
);
assert.match(result.html, /昨日炸板/);
assert.equal(
  result.table.rows[0].previous_board_count,
  null,
  "previous broken cohort has no inferred attempt height",
);
const nextDay = "2026-10-28",
  nextData = {
    ...data,
    date: nextDay,
    current: {
      ...data.current,
      date: nextDay,
      previous_event_date: day,
      items: items.map((r) => ({
        ...r,
        date: nextDay,
        previous_event_date: day,
      })),
    },
    previous_feedback: {
      ...data.previous_feedback,
      date: nextDay,
      previous_date: day,
      items: [],
    },
  };
result = render(
  { 4: { scope: filterScope, value: "broken" } },
  nextData,
  nextDay,
);
assert.equal(
  result.table.rows.length,
  5,
  "previous date filter never leaks to the next date",
);
const source = await fs.readFile(
  new URL("./BrokenBoardAnalysis.tsx", import.meta.url),
  "utf8",
);
assert.match(source, /setHeightSelection\(null\)/);
assert.match(source, /order: "desc"/);
console.log(
  "Broken-board UI: actual prior labels/default height sorting, board-height intersection, unmarked and previous-broken facts, scoped date reset and original queue totals passed",
);
