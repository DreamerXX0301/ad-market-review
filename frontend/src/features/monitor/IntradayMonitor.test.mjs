import assert from "node:assert/strict";
import fs from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const hooks = encode(`
let cells=[], cursor=0, pending=[];
export function reset(){cells=[];pending=[];}
export function begin(){cursor=0;pending=[];}
export function flush(){for(const callback of pending)callback();pending=[];}
export function useState(initial){const i=cursor++;if(!(i in cells))cells[i]=typeof initial==='function'?initial():initial;return[cells[i],value=>{cells[i]=typeof value==='function'?value(cells[i]):value;}];}
export function useRef(initial){const i=cursor++;return cells[i]||(cells[i]={current:initial});}
export function useEffect(callback,deps){const i=cursor++,old=cells[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j]))){pending.push(()=>{old?.cleanup?.();cells[i]={deps,cleanup:callback()};});}}
`);
const api = encode(`
export function usePageActive(){return globalThis.__monitor.active;}
export function useApi(path){const s=globalThis.__monitor;return{data:path?s.data[path]||null:null,error:path?s.errors[path]||'':'',loading:false,reload(){s.reloads.push(path);},setData(value){if(path)s.data[path]=value;}};}
export async function api(path,options){const s=globalThis.__monitor;s.calls.push({path,method:options.method,body:JSON.parse(options.body)});if(s.failure)throw new Error(s.failure);return s.response;}
`);
const ui = encode(`
import{jsx,jsxs}from ${JSON.stringify(runtime)};
export const Badge=({children})=>jsx('b',{children});
export const Panel=({title,subtitle,actions,children})=>jsxs('section',{children:[title,subtitle,actions,children]});
export const Empty=({title,description})=>jsxs('div',{children:[title,description]});
export const ErrorBox=({message})=>jsx('div',{'data-error':true,children:message});
export const Warning=({children})=>jsx('aside',{children});
export function Modal(props){globalThis.__monitor.modal=props;return jsxs('section',{'role':'dialog',children:[props.title,props.children]});}
export const Loading=()=>jsx('p',{children:'loading'});
export const Spinner=()=>jsx('i',{children:'saving'});
export const Stat=({label,value,sub})=>jsxs('div',{children:[label,value,sub]});
export function Table(props){globalThis.__monitor.tables.push(props);return jsx('div',{'data-table':props.rowKey,children:props.rows.length?props.rows.map(row=>jsx('article',{children:props.columns.map(c=>jsx('span',{children:c.render?c.render(row[c.key],row):String(row[c.key]??'—')},c.key))},row[props.rowKey])):jsxs('p',{children:[props.emptyTitle,props.emptyDescription]})});}
`);
const icons = encode(
  `import{jsx}from ${JSON.stringify(runtime)};const Icon=()=>jsx('i',{});export{Icon as Activity,Icon as BellRing,Icon as CheckCheck,Icon as RefreshCw,Icon as Save,Icon as Search};`,
);
const format = encode(
  `export const num=(v,d=2)=>v==null?'—':Number(v).toFixed(d);export const percent=v=>v==null?'—':v+'%';export const tone=()=>'';`,
);
const heat = encode(
  `import{jsx}from ${JSON.stringify(runtime)};export default function Heat(props){globalThis.__monitor.heat=props;return jsx('div',{children:'池内热点视图'});}`,
);
const navSource = ts.transpileModule(
  await fs.readFile(
    new URL("../../components/stockNavigation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
    },
  },
).outputText;
const navigation = encode(navSource);
let source = ts
  .transpileModule(
    await fs.readFile(
      new URL("./IntradayMonitor.tsx", import.meta.url),
      "utf8",
    ),
    {
      fileName: "IntradayMonitor.tsx",
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  )
  .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const modules = {
  react: hooks,
  "../../lib/api": api,
  "../../components/UI": ui,
  "../../lib/format": format,
  "../../components/stockNavigation": navigation,
  "./IntradayMonitorHeat": heat,
  "lucide-react": icons,
  "react/jsx-runtime": runtime,
};
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    match[0],
    "from " +
      JSON.stringify(modules[match[2]] || import.meta.resolve(match[2])),
  );
const {
  default: Monitor,
  monitorSettingsValue,
  confirmedMonitorSettings,
  monitorTime,
  filterMonitorEvents,
  filterMonitorRows,
  currentMonitorTopics,
  monitorFilterIssue,
} = await import(encode(source));
const { begin, flush, reset } = await import(hooks);

const settings = {
  enabled: false,
  revision: 3,
  interval_minutes: 10,
  scopes: {
    tracking: true,
    watchlist: true,
    manual_lines: true,
    gainers_30d: true,
  },
  manual_enabled: true,
  automatic_enabled: true,
};
assert.equal(monitorSettingsValue(null), null);
for (const invalid of [
  { ...settings, enabled: "false" },
  { ...settings, revision: -1 },
  { ...settings, interval_minutes: 5 },
  { ...settings, scopes: { tracking: true } },
  { ...settings, scopes: { ...settings.scopes, gainers_30d: undefined } },
])
  assert.equal(monitorSettingsValue(invalid), null);
const request = {
  expected_revision: 3,
  enabled: true,
  scopes: { ...settings.scopes },
  manual_enabled: true,
  automatic_enabled: true,
};
assert.equal(
  confirmedMonitorSettings(request, {
    ...settings,
    enabled: true,
    revision: 2,
  }),
  null,
);
assert.equal(
  confirmedMonitorSettings(request, { ...settings, revision: 4 }),
  null,
);
assert.equal(
  confirmedMonitorSettings(request, {
    ...settings,
    enabled: true,
    revision: 4,
    manual_enabled: false,
  }),
  null,
);
assert.equal(
  confirmedMonitorSettings(request, { ...settings, enabled: true, revision: 4 })
    .revision,
  4,
);
assert.match(
  monitorTime(Date.parse("2026-11-13T16:01:02Z")),
  /2026\/11\/14.*00:01:02/,
);
assert.equal(monitorTime(null), "—");

const day = "2026-11-16",
  chartDay = "2026-11-13";
const metadata = {
  board: "main",
  topic_date: chartDay,
  metrics_date: chartDay,
  current_topics: [
    { topic_id: "industry-defence", name: "国防军工", kind: "industry" },
    { topic_id: "concept-robot", name: "机器人", kind: "concept" },
  ],
  change_30d_pct: 23.4,
  return_ytd_pct: 35.6,
  limit_up_30d: 2,
  limit_up_1y: 8,
  limit_up_ytd: 5,
  last_limit_up_date: chartDay,
  sessions_since_limit_up: 0,
  coverage_unknown: false,
};
const event = {
  ...metadata,
  event_id: "event-a",
  instrument_id: "600001.SH",
  name: "甲股票",
  code: "600001",
  trade_date: day,
  title: "回踩水平支撑区后收回",
  phase: "holding",
  first_seen_at_ms: Date.parse(day + "T02:00:00Z"),
  confirmed_at_ms: Date.parse(day + "T02:10:00Z"),
  last_seen_at_ms: Date.parse(day + "T02:10:00Z"),
  latest_price: 12.5,
  change_pct: 1,
  unread: true,
  sources: [
    {
      source_kind: "manual",
      label: "我的支撑线",
      lower: 12,
      upper: 12,
      observed_at_ms: Date.parse(day + "T02:00:00Z"),
      source_time_ms: Date.parse(day + "T02:00:00Z"),
    },
    {
      source_kind: "automatic",
      label: "默认水平支撑区",
      lower: 11.9,
      upper: 12.1,
      observed_at_ms: Date.parse(day + "T02:10:00Z"),
      source_time_ms: null,
    },
  ],
};
const eventB = {
  ...event,
  event_id: "event-b",
  instrument_id: "600002.SH",
  name: "乙股票",
  code: "600002",
  phase: "observed",
  sources: [],
  board: "star",
  current_topics: [{ topic_id: "concept-chip", name: "芯片", kind: "concept" }],
  change_pct: null,
  change_30d_pct: null,
  return_ytd_pct: null,
  limit_up_30d: null,
  limit_up_1y: null,
  limit_up_ytd: null,
  last_limit_up_date: null,
  sessions_since_limit_up: null,
  coverage_unknown: true,
};
assert.deepEqual(filterMonitorEvents([event, eventB], "我的支撑线", "all"), [
  event,
]);
assert.deepEqual(filterMonitorEvents([event, eventB], "", "holding"), [event]);
const sunrise = {
  ...event,
  instrument_id: "603366.SH",
  name: "日出东方",
  code: "603366",
  board: "main",
  search_pinyin: "richudongfang",
  search_initials: "rcdf",
};
for (const query of [
  "rc",
  "RCDF",
  "ｒｃ",
  "ri chu",
  "richudongfang",
  "日出",
  "603366",
]) {
  assert.deepEqual(
    filterMonitorRows([sunrise, eventB], query),
    [sunrise],
    query,
  );
}
assert.deepEqual(filterMonitorEvents([sunrise, eventB], "rc", "holding"), [
  sunrise,
]);
assert.deepEqual(
  filterMonitorRows([sunrise, eventB], "rc", { board: "star" }),
  [],
);
assert.deepEqual(filterMonitorRows([eventB], "乙股票"), [eventB]);
assert.deepEqual(filterMonitorRows([sunrise], "cdf"), []);
const zero = { ...event, change_pct: 0, limit_up_1y: 8, limit_up_ytd: 0 };
const unknown = {
  ...eventB,
  board: null,
  topic_date: "2026-11-12",
  current_topics: metadata.current_topics,
};
const beijing = { ...zero, instrument_id: "920001.BJ", board: "beijing" };
const aliasBeijing = { ...zero, instrument_id: "830001.BJ", board: "bse" };
const filterRows = [zero, unknown, beijing, aliasBeijing];
const beforeFilters = structuredClone(filterRows);
assert.deepEqual(
  filterMonitorRows(filterRows, "", { board: "beijing" }, chartDay),
  [beijing, aliasBeijing],
);
assert.deepEqual(
  filterMonitorRows(filterRows, "", { board: "bse" }, chartDay),
  [beijing, aliasBeijing],
);
assert.deepEqual(
  filterMonitorRows(filterRows, "", { board: "unknown" }, chartDay),
  [unknown],
);
assert.deepEqual(
  filterMonitorRows(
    filterRows,
    "甲",
    { topicQuery: "concept-robot", board: "main" },
    chartDay,
  ),
  [zero],
);
assert.deepEqual(
  currentMonitorTopics(unknown, chartDay),
  [],
  "stale classifications do not become current filters",
);
assert.deepEqual(
  filterMonitorRows(
    [
      zero,
      {
        ...zero,
        current_topics: [
          { topic_id: "concept-robotics", name: "机器人制造", kind: "concept" },
        ],
      },
    ],
    "",
    { topicId: "concept-robot" },
    chartDay,
  ),
  [zero],
  "group drilldown uses an exact topic id instead of a name/id substring",
);
assert.deepEqual(
  filterMonitorRows(
    [
      zero,
      {
        ...zero,
        current_topics: [
          { topic_id: "concept-robot", name: "机器人", kind: "concept" },
        ],
      },
    ],
    "",
    {
      topicId: "concept-robot",
      topicScope: [{ topic_id: "industry-defence", name: "国防军工" }],
    },
    chartDay,
  ),
  [zero],
  "nested heat group drilldown retains earlier classification constraints",
);
assert.deepEqual(
  filterMonitorRows(
    [zero, unknown],
    "",
    { metric: "change_pct", min: "0", max: "0" },
    chartDay,
  ),
  [zero],
  "numeric zero is real and null cannot become zero",
);
assert.deepEqual(
  filterMonitorRows(
    [zero, unknown],
    "",
    { metric: "change_pct", min: "0", max: "0", unknown: "include" },
    chartDay,
  ),
  [zero, unknown],
);
assert.deepEqual(
  filterMonitorRows(
    [zero, unknown],
    "",
    { metric: "change_pct", unknown: "only" },
    chartDay,
  ),
  [unknown],
);
assert.deepEqual(
  filterMonitorRows([zero, unknown], "", {}, chartDay),
  [zero, unknown],
  "unset bounds retain unknowns",
);
assert.deepEqual(
  filterMonitorRows([zero], "", { metric: "limit_up_1y", min: "5" }, chartDay),
  [zero],
);
assert.deepEqual(
  filterMonitorRows([zero], "", { metric: "limit_up_ytd", min: "5" }, chartDay),
  [],
  "rolling year and calendar year stay distinct",
);
assert.deepEqual(
  filterMonitorRows(
    [zero, unknown],
    "",
    { metric: "last_limit_up_date", min: chartDay, max: chartDay },
    chartDay,
  ),
  [zero],
);
assert.deepEqual(
  filterMonitorRows([zero], "", { min: "5", max: "1" }, chartDay),
  [],
);
assert.deepEqual(
  filterMonitorRows([zero], "", { min: "Infinity" }, chartDay),
  [],
);
assert.match(monitorFilterIssue({ min: "5", max: "1" }), /最小值/);
assert.match(
  monitorFilterIssue({ metric: "last_limit_up_date", min: "2026-02-30" }),
  /有效的涨停日期/,
);
assert.deepEqual(
  filterRows,
  beforeFilters,
  "view filters do not mutate source facts",
);
const eventPath = `/intraday-monitor/events?date=${day}&limit=200`;
const revisionPath = "/intraday-monitor/revision";
const overview = {
  revision: "batch-one",
  date: day,
  chart_as_of: chartDay,
  settings,
  status: {
    state: "disabled",
    reason: "等待保存开启",
    can_enable: true,
    worker_alive: true,
  },
  counts: {
    stocks: 2,
    eligible: 1,
    excluded: 1,
    tracking: 2,
    watchlist: 1,
    manual_lines: 1,
    gainers_30d: null,
  },
  last_batch: null,
  stocks: [
    {
      ...metadata,
      instrument_id: event.instrument_id,
      name: event.name,
      code: event.code,
      origins: ["tracking", "watchlist"],
      strategy_count: 2,
      latest_price: null,
      change_pct: null,
      last_observed_at_ms: null,
      state: "pending",
      level_count: 2,
    },
  ],
  events: { unread_count: 2 },
  retention: { samples_sessions: 20, events_sessions: 60 },
};
const oldWindow = globalThis.window,
  oldDocument = globalThis.document;
const timers = [];
globalThis.window = {
  setInterval(callback, interval) {
    timers.push({ callback, interval });
    return timers.length;
  },
  clearInterval() {},
  dispatchEvent(event) {
    globalThis.__monitor.notifications.push(event.type);
    return true;
  },
};
globalThis.document = { visibilityState: "visible" };
globalThis.__monitor = {
  active: true,
  data: {
    "/intraday-monitor": overview,
    [revisionPath]: { date: day, revision: "batch-one" },
    [eventPath]: {
      date: day,
      chart_as_of: chartDay,
      items: [event, eventB],
      total: 234,
      unread_count: 2,
    },
  },
  errors: {},
  reloads: [],
  calls: [],
  tables: [],
  response: null,
  notifications: [],
};
const state = globalThis.__monitor;
const opened = [];
function walk(node, predicate) {
  if (Array.isArray(node))
    return node.flatMap((child) => walk(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [
    ...(predicate(node) ? [node] : []),
    ...walk(node.props.children, predicate),
    ...walk(node.props.actions, predicate),
  ];
}
const find = (tree, predicate) => walk(tree, predicate)[0];
const text = (node) => renderToStaticMarkup(node).replace(/<[^>]*>/g, "");
function button(tree, title) {
  const value = find(
    tree,
    (node) => node.type === "button" && text(node) === title,
  );
  assert.ok(value, title);
  return value;
}
function render() {
  state.tables.length = 0;
  state.modal = null;
  begin();
  let tree = Monitor({ onStock: (id, nav) => opened.push({ id, nav }) });
  flush();
  begin();
  tree = Monitor({ onStock: (id, nav) => opened.push({ id, nav }) });
  flush();
  const html = renderToStaticMarkup(tree);
  return { tree, html };
}
try {
  let result = render();
  assert.match(result.html, /尚未采样/);
  assert.match(result.html, /已加载 2 \/ 234/);
  assert.doesNotMatch(
    result.html,
    /<details/,
    "table cells do not expand row height",
  );
  assert.doesNotMatch(result.html, /来源批次时间/);
  const eventTable = state.tables.at(-1);
  button(
    eventTable.columns
      .find((c) => c.key === "sources")
      .render(event.sources, event),
    "查看 2 项依据",
  ).props.onClick();
  result = render();
  assert.match(result.html, /role="dialog"/);
  assert.match(result.html, /手动画线/);
  assert.match(result.html, /默认价位/);
  assert.match(result.html, /来源批次时间 —/);
  assert.match(result.html, /单股成交时间未知/);
  assert.match(result.html, /采样价 12\.5000/);
  state.modal.onClose();
  result = render();
  assert.doesNotMatch(result.html, /role="dialog"/);
  assert.match(result.html, /title="[^"]+10:00:00"/);
  assert.match(result.html, />10:00:00<\/span>/);
  assert.equal(state.calls.length, 0, "rendering only reads saved data");
  assert.equal(timers.at(-1).interval, 30_000);
  timers.at(-1).callback();
  assert.deepEqual(state.reloads, [revisionPath, eventPath]);
  state.data[revisionPath] = { date: day, revision: "batch-two" };
  result = render();
  assert.equal(state.reloads.at(-1), "/intraday-monitor");
  globalThis.document.visibilityState = "hidden";
  timers.at(-1).callback();
  assert.equal(state.reloads.length, 3, "hidden page does not poll");
  globalThis.document.visibilityState = "visible";
  button(result.tree, "刷新已保存结果").props.onClick();
  assert.equal(state.reloads.length, 5);
  assert.equal(state.calls.length, 0, "refresh cannot create a capture");

  // The same independent filters are wired to both event and stock tables.
  find(
    result.tree,
    (node) => node.props["aria-label"] === "搜索行业或概念",
  ).props.onChange({ target: { value: "机器人" } });
  result = render();
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.event_id),
    [event.event_id],
  );
  button(result.tree, "重置筛选").props.onClick();
  result = render();
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选股票板型",
  ).props.onChange({ target: { value: "star" } });
  result = render();
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.event_id),
    [eventB.event_id],
  );
  button(result.tree, "重置筛选").props.onClick();
  result = render();
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选指标",
  ).props.onChange({ target: { value: "limit_up_1y" } });
  result = render();
  find(
    result.tree,
    (node) => node.props["aria-label"] === "指标最小值",
  ).props.onChange({ target: { value: "5" } });
  result = render();
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.event_id),
    [event.event_id],
  );
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选未知指标",
  ).props.onChange({ target: { value: "only" } });
  result = render();
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.event_id),
    [eventB.event_id],
  );
  button(result.tree, "重置筛选").props.onClick();
  result = render();
  const factsCell = state.tables
    .at(-1)
    .columns.find((c) => c.key === "change_30d_pct")
    .render(event.change_30d_pct, event);
  button(factsCell.type(factsCell.props), "详情").props.onClick();
  result = render();
  assert.match(result.html, /近30交易日/);
  assert.match(result.html, /年内涨幅/);
  assert.match(result.html, /最近涨停/);
  assert.match(result.html, /近一年 8 次 · 年内 5 次/);
  state.modal.onClose();
  result = render();
  const topicCell = state.tables
    .at(-1)
    .columns.find((c) => c.key === "current_topics")
    .render(event.current_topics, event);
  button(topicCell.type(topicCell.props), "全部 2 项").props.onClick();
  result = render();
  assert.match(result.html, /当前行业与概念/);
  assert.match(result.html, /不代表提醒发生时的历史归属/);
  state.modal.onClose();
  result = render();

  // Heat observes the current filtered stock set, not events or the full pool.
  const beforeHeat = state.data["/intraday-monitor"];
  const stockA = beforeHeat.stocks[0];
  const stockB = {
    ...stockA,
    instrument_id: "688002.SH",
    name: "科创样本",
    code: "688002",
    board: "star",
    change_30d_pct: 5,
  };
  const stockC = {
    ...stockA,
    instrument_id: "600003.SH",
    name: "近名概念股",
    code: "600003",
    change_30d_pct: 25,
    current_topics: [
      { topic_id: "concept-robotics", name: "机器人制造", kind: "concept" },
    ],
  };
  state.data["/intraday-monitor"] = {
    ...beforeHeat,
    stocks: [stockA, stockB, stockC],
    counts: { ...beforeHeat.counts, stocks: 3 },
  };
  const beforeHeatCalls = state.calls.length;
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选股票板型",
  ).props.onChange({ target: { value: "main" } });
  result = render();
  button(result.tree, "池内热点").props.onClick();
  result = render();
  assert.deepEqual(
    state.heat.stocks.map((row) => row.instrument_id),
    [stockA.instrument_id, stockC.instrument_id],
  );
  assert.equal(state.heat.topicDate, chartDay);
  assert.equal(state.heat.tradeDate, day);
  assert.equal(state.heat.events, state.data[eventPath].items);
  assert.equal(state.heat.eventsTotal, 234);
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选股票板型",
  ).props.onChange({ target: { value: "all" } });
  result = render();
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选指标",
  ).props.onChange({ target: { value: "change_30d_pct" } });
  result = render();
  find(
    result.tree,
    (node) => node.props["aria-label"] === "指标最小值",
  ).props.onChange({ target: { value: "20" } });
  result = render();
  assert.deepEqual(
    state.heat.stocks.map((row) => row.instrument_id),
    [stockA.instrument_id, stockC.instrument_id],
  );
  state.heat.onTopic({ topic_id: "concept-robot", name: "机器人" });
  result = render();
  assert.equal(button(result.tree, "监控股票").props["aria-pressed"], true);
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.instrument_id),
    [stockA.instrument_id],
  );
  const stockColumns = state.tables.at(-1).columns;
  assert.deepEqual(
    stockColumns.slice(0, 4).map((column) => column.key),
    ["name", "change_pct", "latest_price", "last_observed_at_ms"],
  );
  const sampledChange = stockColumns[1];
  assert.equal(sampledChange.sortable, true, "sort stays available with missing samples");
  assert.match(text(sampledChange.render(0)), /^0(?:\.00)?%$/);
  assert.equal(text(sampledChange.render(null)), "—");
  assert.match(result.html, /本交易日最近一轮采样/);
  assert.match(result.html, /限定分组：机器人/);
  assert.equal(
    find(result.tree, (node) => node.props["aria-label"] === "指标最小值").props
      .value,
    "20",
    "drilldown preserves the current range",
  );
  assert.equal(
    state.calls.length,
    beforeHeatCalls,
    "heat and group drilldown never create network or tracking mutations",
  );
  button(result.tree, "重置筛选").props.onClick();
  const boardPath = `/intraday-monitor/board-observations?date=${day}`;
  state.data[boardPath] = {
    date: day,
    observed_at: `${day}T10:00:05+08:00`,
    source_label: "扶摇 · 盘中观察",
    notice: "仅核验已留存的板况快照",
    items: [
      { instrument_id: stockA.instrument_id, type: "resealed" },
      { instrument_id: stockB.instrument_id, type: "broken" },
      { instrument_id: "000001.SZ", type: "limit_down" },
      { instrument_id: stockC.instrument_id, type: "unknown" },
    ],
  };
  result = render();
  button(result.tree, "板况观察").props.onClick();
  result = render();
  assert.match(result.html, /较上一档回封/);
  assert.match(result.html, /炸板/);
  assert.match(result.html, /扶摇 · 盘中观察/);
  assert.match(result.html, /10:00:05/);
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.instrument_id),
    [stockA.instrument_id, stockB.instrument_id],
    "only known observations belonging to the monitoring set may appear",
  );
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选板况",
  ).props.onChange({ target: { value: "broken" } });
  result = render();
  assert.deepEqual(
    state.tables.at(-1).rows.map((row) => row.instrument_id),
    [stockB.instrument_id],
  );
  timers.at(-1).callback();
  assert.equal(state.reloads.at(-1), boardPath);
  assert.equal(state.calls.length, beforeHeatCalls);
  state.data[boardPath] = { ...state.data[boardPath], date: chartDay };
  result = render();
  assert.match(result.html, /当天暂无可核验的板况快照/);
  assert.equal(state.tables.at(-1).rows.length, 0);
  state.data["/intraday-monitor"] = beforeHeat;
  result = render();
  button(result.tree, "今日提醒").props.onClick();
  result = render();
  timers.at(-1).callback();
  assert.notEqual(state.reloads.at(-1), boardPath, "other views do not poll boards");

  const table = state.tables.at(-1);
  const contaminated = {
    ...event,
    eventBaseline: { rawClose: 999 },
    historicalChecks: [{ bad: true }],
  };
  table.onStockNavigate(
    event.instrument_id,
    [eventB, contaminated],
    contaminated,
  );
  assert.equal(
    opened[0].nav.asOf,
    chartDay,
    "chart uses published date, not today's intraday date",
  );
  assert.deepEqual(
    opened[0].nav.items.map((item) => item.id),
    [eventB.instrument_id, event.instrument_id],
  );
  assert.equal("eventBaseline" in opened[0].nav.items[1], false);
  assert.equal("historicalChecks" in opened[0].nav.items[1], false);

  find(
    result.tree,
    (node) => node.type === "input" && node.props.type === "search",
  ).props.onChange({ target: { value: "我的支撑线" } });
  result = render();
  state.response = { updated: 1 };
  await button(result.tree, "当前结果标为已读").props.onClick();
  assert.deepEqual(state.calls.at(-1), {
    path: "/intraday-monitor/events/read",
    method: "POST",
    body: { event_ids: [event.event_id] },
  });

  button(result.tree, "采集设置").props.onClick();
  result = render();
  assert.match(result.html, /30日涨幅榜前1000/);
  assert.match(result.html, /待准备/);
  assert.equal(
    walk(
      result.tree,
      (node) => node.type === "input" && node.props.type === "checkbox",
    )[4].props.checked,
    true,
    "gainers scope is read from API without enabling the collector",
  );
  // Eligibility describes the saved scope. A user may repair that scope in
  // this draft; the server must validate the complete submitted payload.
  state.data["/intraday-monitor"] = {
    ...state.data["/intraday-monitor"],
    status: { ...state.data["/intraday-monitor"].status, can_enable: false },
  };
  result = render();
  const enable = find(
    result.tree,
    (node) => node.type === "input" && node.props.type === "checkbox",
  );
  assert.equal(enable.props.disabled, false);
  enable.props.onChange({ target: { checked: true } });
  result = render();
  state.response = { ...settings, enabled: true, revision: 4 };
  await button(result.tree, "保存采集设置").props.onClick();
  assert.deepEqual(state.calls.at(-1), {
    path: "/intraday-monitor/settings",
    method: "PUT",
    body: request,
  });
  assert.equal(state.data["/intraday-monitor"].settings.enabled, true);
  assert.deepEqual(state.notifications, [
    "intraday-monitor-changed",
    "intraday-monitor-changed",
  ]);
  result = render();
  assert.match(result.html, /设置已保存/);

  // Concurrent revision and inactive pages cannot save stale edits.
  find(
    result.tree,
    (node) => node.type === "input" && node.props.type === "checkbox",
  ).props.onChange({ target: { checked: false } });
  state.data["/intraday-monitor"] = {
    ...state.data["/intraday-monitor"],
    settings: { ...settings, enabled: true, revision: 5 },
  };
  result = render();
  assert.equal(button(result.tree, "保存采集设置").props.disabled, true);
  const callsBefore = state.calls.length;
  await button(result.tree, "保存采集设置").props.onClick();
  assert.equal(state.calls.length, callsBefore);
  assert.match(result.html, /设置已在其他位置变化/);
  state.active = false;
  result = render();
  await button(result.tree, "保存采集设置").props.onClick();
  assert.equal(state.calls.length, callsBefore);

  // An old-day response cannot populate today's table or mark records read.
  state.active = true;
  button(result.tree, "今日提醒").props.onClick();
  state.data[eventPath] = { ...state.data[eventPath], date: "2026-11-12" };
  result = render();
  assert.match(result.html, /提醒日期与当前监控日期不一致/);
  assert.doesNotMatch(result.html, /回踩水平支撑区后收回/);
  assert.equal(button(result.tree, "当前结果标为已读").props.disabled, true);
  assert.equal(state.tables.at(-1).rows.length, 0);
  button(result.tree, "监控股票").props.onClick();
  state.data["/intraday-monitor"] = {
    ...state.data["/intraday-monitor"],
    chart_as_of: null,
  };
  result = render();
  assert.equal(
    state.tables.at(-1).onStockNavigate,
    undefined,
    "no chart date is invented",
  );
  assert.match(result.html, /等待采样/);
  assert.doesNotMatch(result.html, /0\.0000/);
  assert.equal(
    find(result.tree, (node) => node.props["aria-label"] === "搜索行业或概念")
      .props.value,
    "",
  );
  find(
    result.tree,
    (node) => node.props["aria-label"] === "筛选股票板型",
  ).props.onChange({ target: { value: "star" } });
  result = render();
  assert.equal(
    state.tables.at(-1).rows.length,
    0,
    "stock list uses the same board filter",
  );
  // Direct links choose only known monitor views; unrelated values cannot
  // bypass the normal tabs or turn a page visit into a collector mutation.
  for (const [search, expected] of [
    ["?monitor_view=heat", "池内热点"],
    ["?monitor_view=stocks", "监控股票"],
    ["?monitor_view=not-a-view", "今日提醒"],
  ]) {
    reset();
    globalThis.window.location = { search };
    result = render();
    assert.equal(button(result.tree, expected).props["aria-pressed"], true);
  }
  console.log(
    "intraday monitor: saved-read polling, date isolation, published chart navigation, multi-source evidence, explicit read mutation and revision-confirmed settings passed",
  );
} finally {
  reset();
  globalThis.window = oldWindow;
  globalThis.document = oldDocument;
  delete globalThis.__monitor;
}
