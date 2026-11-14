import assert from "node:assert/strict";
import fs from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, fileName = "test.ts") =>
  ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const runtime = import.meta.resolve("react/jsx-runtime");
const hooks = encode(`
let cells=[],cursor=0;
export function reset(){cells=[];cursor=0;}
export function begin(){cursor=0;}
export function useState(initial){const i=cursor++;if(!(i in cells))cells[i]=typeof initial==='function'?initial():initial;return[cells[i],value=>{cells[i]=typeof value==='function'?value(cells[i]):value;}];}
export function useMemo(callback){return callback();}
`);
const ui = encode(`
import{jsx,jsxs}from ${JSON.stringify(runtime)};
export const Badge=({children})=>jsx('b',{children});
export const Warning=({children})=>jsx('aside',{children});
export function Table(props){globalThis.__heatView.tables.push(props);return jsx('div',{'data-table':props.rowKey,children:props.rows.length?props.rows.map(row=>jsx('article',{children:props.columns.map(column=>jsx('span',{children:column.render?column.render(row[column.key],row):String(row[column.key]??'—')},column.key))},row[props.rowKey])):jsxs('p',{children:[props.emptyTitle,props.emptyDescription]})});}
`);
const format = encode(`
export const num=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);
export const percent=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':(v>0?'+':'')+Number(v).toFixed(d)+'%';
export const tone=value=>value>0?'up':value<0?'down':'muted';
`);
const heat = encode(
  compile(
    await fs.readFile(new URL("./monitorHeat.ts", import.meta.url), "utf8"),
  ),
);
const componentSource = await fs.readFile(
  new URL("./IntradayMonitorHeat.tsx", import.meta.url),
  "utf8",
);
let source = compile(componentSource, "IntradayMonitorHeat.tsx").replace(
  /^import ["'][^"']+\.css["'];?$/gm,
  "",
);
const modules = {
  react: hooks,
  "../../components/UI": ui,
  "../../lib/format": format,
  "./monitorHeat": heat,
  "react/jsx-runtime": runtime,
};
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    match[0],
    "from " +
      JSON.stringify(modules[match[2]] || import.meta.resolve(match[2])),
  );
const { default: Heat } = await import(encode(source));
const { begin, reset } = await import(hooks);
assert.doesNotMatch(componentSource, /useApi\(|fetch\(|method:|requestKey\(/);

const day = "2026-11-16";
const topicDate = "2026-11-13";
function stock(index, concept, industry, intraday, month, year, alerts = 0) {
  const code = `600${String(index).padStart(3, "0")}`;
  return {
    instrument_id: `${code}.SH`,
    name: `测试股票${index}`,
    code,
    trade_date: day,
    topic_date: topicDate,
    metrics_date: topicDate,
    current_topics: [
      { topic_id: `concept-${concept}`, name: concept, kind: "concept" },
      { topic_id: `industry-${industry}`, name: industry, kind: "industry" },
    ],
    change_pct: intraday,
    change_30d_pct: month,
    return_ytd_pct: year,
    today_alert_count: alerts,
    state: "unknown",
  };
}
const stocks = [
  stock(1, "机器人", "电气设备", 4, 10, 100, 1),
  stock(2, "机器人", "电气设备", 0, 20, 20),
  stock(3, "机器人", "电气设备", -1, 30, 30),
  stock(4, "芯片", "电子", 5, 60, 80),
  stock(5, "芯片", "电子", 3, 80, 100),
  stock(6, "独立概念", "医药", 2, 90, 5),
  stock(7, "未采样概念", "未采样行业", null, 15, 12),
  stock(8, "未采样概念", "未采样行业", null, 25, 14),
];
const originals = structuredClone(stocks);
const events = [
  {
    event_id: "event-one",
    instrument_id: stocks[0].instrument_id,
    trade_date: day,
  },
  {
    event_id: "event-two",
    instrument_id: stocks[0].instrument_id,
    trade_date: day,
  },
];
const topicsOpened = [];
const stocksOpened = [];
let props = {
  stocks,
  events,
  topicDate,
  tradeDate: day,
  chartAsOf: topicDate,
  eventsLoaded: 2,
  eventsTotal: 20,
  onTopic: (group) => topicsOpened.push(group),
  onStock: (id, rows) => stocksOpened.push({ id, rows }),
};
globalThis.__heatView = { tables: [] };
const state = globalThis.__heatView;
const priorWindow = globalThis.window;
function walk(node, predicate) {
  if (Array.isArray(node))
    return node.flatMap((child) => walk(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [
    ...(predicate(node) ? [node] : []),
    ...walk(node.props.children, predicate),
  ];
}
const find = (tree, predicate) => walk(tree, predicate)[0];
function control(tree, label) {
  const node = find(tree, (item) => item.props["aria-label"] === label);
  assert.ok(node, label);
  return node;
}
function render() {
  state.tables.length = 0;
  begin();
  const tree = Heat(props);
  const html = renderToStaticMarkup(tree);
  return { tree, html, table: state.tables.at(-1) };
}
function change(result, label, value) {
  control(result.tree, label).props.onChange({ target: { value } });
  return render();
}
try {
  let result = render();
  assert.equal(control(result.tree, "池内热点周期").props.value, "intraday");
  assert.equal(control(result.tree, "池内热点分类").props.value, "concept");
  assert.equal(control(result.tree, "池内热点最少有效样本").props.value, 3);
  assert.equal(result.table.rowKey, "topic_id");
  assert.deepEqual(
    result.table.rows.map((group) => group.name),
    ["机器人"],
  );
  assert.equal(result.table.rows[0].stock_count, 3);
  assert.equal(
    result.table.rows[0].valid_count,
    3,
    "a valid zero and unknown level state are still numeric samples",
  );
  assert.equal(result.table.rows[0].median_pct, 0);
  assert.equal(result.table.rows[0].strong_count, 1);
  assert.equal(result.table.rows[0].alert_stock_count, 1);
  assert.match(result.html, /当前筛选 8 只/);
  assert.match(result.html, /有效涨幅 6 只/);
  assert.match(result.html, /未知样本单列覆盖/);
  assert.match(result.html, /非涨停统计|不是涨停家数/);
  assert.match(result.html, /不是全市场板块指数涨幅或资金流/);
  assert.match(result.html, /各组数量不可相加/);
  assert.match(result.html, /全部已保存当日提醒/);

  const group = result.table.rows[0];
  const topicColumn = result.table.columns.find(
    (column) => column.key === "name",
  );
  const topicButton = control(
    topicColumn.render(group.name, group),
    "查看机器人监控股票",
  );
  topicButton.props.onClick();
  assert.equal(
    topicsOpened.at(-1),
    group,
    "drilldown passes the actual selected group",
  );
  const leaderColumn = result.table.columns.find(
    (column) => column.key === "leaders",
  );
  const leader = group.leaders[0];
  const leaderButton = control(
    leaderColumn.render(group.leaders, group),
    `查看${leader.name}K线`,
  );
  assert.equal(leaderButton.props.disabled, false);
  leaderButton.props.onClick();
  assert.equal(stocksOpened.at(-1).id, leader.instrument_id);
  assert.equal(
    stocksOpened.at(-1).rows,
    group.stocks,
    "leader navigation uses the selected group's complete stock order",
  );

  result = change(result, "池内热点周期", "30d");
  assert.equal(result.table.rows[0].median_pct, 20);
  assert.match(result.html, /近30交易日有效涨幅 8 只/);
  result = change(result, "池内热点分类", "industry");
  assert.deepEqual(
    result.table.rows.map((row) => row.name),
    ["电气设备"],
  );
  assert.equal(result.table.columns[0].title, "池内行业");
  result = change(result, "池内热点最少有效样本", "1");
  assert.equal(result.table.rows.length, 4);
  assert.equal(result.table.rows[0].name, "医药");
  result = change(result, "池内热点周期", "ytd");
  assert.equal(result.table.rows[0].name, "电子");
  result = change(result, "池内热点最少有效样本", "5");
  assert.equal(result.table.rows.length, 0);
  assert.match(result.html, /暂无达到5只有效样本的分组/);

  // A no-sample intraday view is explicitly unknown, with a published-period escape.
  reset();
  props = {
    ...props,
    stocks: stocks.map((row) => ({ ...row, change_pct: null })),
  };
  result = render();
  assert.equal(result.table.rows.length, 0);
  assert.match(result.html, /尚无可用的盘中涨幅/);
  assert.match(result.html, /强弱保持未知/);
  assert.match(result.html, /可切换/);
  assert.doesNotMatch(result.html, /0\.0+%/);
  for (const key of [
    "up_count",
    "strong_count",
    "up_ratio_pct",
    "median_pct",
  ]) {
    const column = result.table.columns.find(
      (candidate) => candidate.key === key,
    );
    const output = column.render(null, { median_pct: null, mean_pct: null });
    assert.match(
      React.isValidElement(output)
        ? renderToStaticMarkup(output)
        : String(output),
      /—/,
    );
  }
  result = change(result, "池内热点周期", "30d");
  assert.equal(result.table.rows.length, 1);
  assert.equal(result.table.rows[0].median_pct, 20);

  // Old payloads disclose the bounded loaded-event scope instead of claiming completeness.
  reset();
  props = {
    ...props,
    stocks: stocks.map(({ today_alert_count, ...row }) => row),
    chartAsOf: undefined,
  };
  result = render();
  assert.match(result.html, /已加载提醒范围（2 \/ 20条）/);
  assert.match(result.html, /可能低于全部持久提醒数量/);
  const fallbackGroup = result.table.rows[0];
  const fallbackLeaderColumn = result.table.columns.find(
    (column) => column.key === "leaders",
  );
  const disabledLeader = control(
    fallbackLeaderColumn.render(fallbackGroup.leaders, fallbackGroup),
    `查看${fallbackGroup.leaders[0].name}K线`,
  );
  assert.equal(
    disabledLeader.props.disabled,
    true,
    "unpublished daily chart dates are not invented",
  );
  assert.deepEqual(
    stocks,
    originals,
    "view controls and drilldowns never mutate the monitored pool",
  );
  for (const [requested, expected] of [
    ["30d", "30d"],
    ["ytd", "ytd"],
    ["intraday", "intraday"],
    ["unknown", "intraday"],
  ]) {
    reset();
    globalThis.window = {
      location: { search: `?monitor_period=${requested}` },
    };
    props = { ...props, stocks, chartAsOf: topicDate };
    result = render();
    assert.equal(control(result.tree, "池内热点周期").props.value, expected);
    result = change(result, "池内热点周期", "intraday");
    assert.equal(control(result.tree, "池内热点周期").props.value, "intraday");
  }
  console.log(
    "monitor heat view: local period/kind/sample controls, coverage and unknown copy, topic drilldown, leader chart scope and bounded alert fallback passed",
  );
} finally {
  reset();
  if (priorWindow === undefined) delete globalThis.window;
  else globalThis.window = priorWindow;
  delete globalThis.__heatView;
}
