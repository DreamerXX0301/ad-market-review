import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encoded = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = async (path, replacements = {}) => {
  let source = ts
    .transpileModule(
      await fs.readFile(new URL(path, import.meta.url), "utf8"),
      {
        fileName: path,
        compilerOptions: {
          module: ts.ModuleKind.ES2022,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      },
    )
    .outputText.replace(/import "[^"]+\.css";/g, "");
  for (const [key, value] of Object.entries({
    react: import.meta.resolve("react"),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    ...replacements,
  }))
    source = source.replaceAll(
      `from ${JSON.stringify(key)}`,
      `from ${JSON.stringify(value)}`,
    );
  return encoded(source);
};
const helpersURL = await compile("./lineAlertPresentation.ts");
const {
  lineAlertView,
  lineAlertInScope,
  filterLineAlerts,
  lineAlertWatchlistState,
  lineCheckRefreshKeys,
  lineAlertPrice,
  lineAlertSource,
  lineAlertChecks,
} = await import(helpersURL);
const event = Object.freeze({
  event_id: "event-1",
  instrument_id: "605089.SH",
  name: "味知香",
  code: "605089",
  annotation_id: "line-1",
  annotation_revision: 2,
  line_type: "support",
  rule: "support_reclaim",
  trade_date: "2026-10-20",
  line_price: 23.25,
  price_basis: "none",
  anchor_date: null,
  previous_close: 23.5,
  open: 23.45,
  high: 23.8,
  low: 23.2,
  close: 23.6,
  state: "triggered",
  title: "回踩支撑后站回",
  reason: "最低价回踩23.25，收盘23.60收回支撑",
  created_at_ms: 1792592249740,
  added_to_watchlist: true,
  watchlist_add_state: "added",
  watchlist_added_business_date: "2026-10-21",
});
const invalid = Object.freeze({
  ...event,
  event_id: "event-risk",
  rule: "support_breakdown",
  close: 23.1,
  state: "invalidated",
  added_to_watchlist: false,
  watchlist_add_state: "not_eligible",
  reason: "收盘23.10严格跌破支撑23.25，单独记录失守",
});
assert.equal(
  lineAlertPrice({ ...event, line_price: 100, comparison_line_price: 50 }),
  50,
);
assert.equal(
  lineAlertPrice({ ...event, comparison_line_price: null }),
  null,
  "an explicitly unknown comparable price cannot fall back to the saved anchor price",
);
assert.equal(lineAlertInScope(event, "2026-10-19"), false);
assert.equal(lineAlertInScope(event, "2026-10-20"), true);
assert.equal(
  lineAlertInScope({ ...event, trade_date: null }, "2026-10-21"),
  false,
);
assert.equal(lineAlertView(invalid).invalidated, true);
assert.equal(
  lineAlertView({ ...invalid, state: "triggered" }).invalidated,
  true,
  "a breakdown can never be presented as a successful support reclaim",
);
assert.equal(lineAlertView({ ...event, rule: "pressure_touch" }).tone, "touch");
const automatic = Object.freeze({
  ...event,
  event_id: "auto-stop",
  source_kind: "automatic_price_volume",
  rule: "stop_breakdown",
  state: "invalidated",
  previous_close: 24,
  line_price: 23.25,
  close: 23.1,
  level_date: "2026-10-19",
  revision: 3,
  algorithm: "structure-volume-levels-v1",
  level_evidence: {
    as_of: "2026-10-19",
    snapshot_id: "prior-frozen-source",
  },
  market_snapshot_id: "event-source",
  automatic_level: {
    source: "confirmed_pivot_low",
    formed_at: "2026-10-15",
    confirmed_on: "2026-10-19",
    base_price: 23.75,
    buffer_atr: 0.5,
    buffer_value: 0.5,
    atr_reference_date: "2026-10-19",
  },
  watchlist_add_state: "disabled",
});
assert.equal(lineAlertSource(event), "手动画线");
assert.equal(lineAlertSource(automatic), "自动量价");
assert.equal(lineAlertView(automatic).stateLabel, "止损位跌破");
assert.equal(
  lineAlertView({ ...automatic, state: "triggered" }).invalidated,
  true,
);
assert.match(lineAlertWatchlistState(automatic), /仅提醒.*不自动入池/);
assert.deepEqual(filterLineAlerts([event, automatic], "自动量价", "all"), [
  automatic,
]);
assert.deepEqual(
  lineAlertChecks({
    latest_checks: {
      manual_line: { job_id: "manual" },
      automatic_price_volume: { job_id: "auto", stale: true },
    },
  }).map(({ check }) => check.job_id),
  ["manual", "auto"],
);
assert.deepEqual(
  filterLineAlerts(
    [
      event,
      invalid,
      { ...event, event_id: "future", trade_date: "2026-10-22" },
    ],
    "味知香",
    "triggered",
    "2026-10-21",
  ),
  [event],
);
assert.deepEqual(
  filterLineAlerts([event, invalid], "收盘失守", "invalidated", "2026-10-21"),
  [invalid],
);
assert.match(lineAlertWatchlistState(invalid), /失守.*不自动/);
assert.match(
  lineAlertWatchlistState({
    ...event,
    watchlist_add_state: "missing_current_quote",
  }),
  /待核验.*尚未加入/,
);
assert.match(
  lineAlertWatchlistState({ ...event, watchlist_add_state: undefined }),
  /待确认/,
);
assert.deepEqual(
  lineCheckRefreshKeys([
    {
      job_id: "check1",
      kind: "line_alert_check",
      status: "succeeded",
      finished_at_ms: 123,
    },
    { job_id: "other", kind: "selection", status: "succeeded" },
    {
      job_id: "automatic",
      operation: "automatic_price_alert_check",
      status: "succeeded",
      finished_at_ms: 456,
    },
    { job_id: "running", kind: "line_alert_check", status: "running" },
  ]),
  ["check1:123", "automatic:456"],
);
const ui =
  encoded(`import {createElement as h} from ${JSON.stringify(import.meta.resolve("react"))};
 export const useToast=()=>()=>{};
 export const Badge=({children})=>h('span',null,children);
 export const Panel=({children,title,actions})=>h('section',null,h('h2',null,title),actions,children);
 export const Empty=({title,description})=>h('div',null,title,description);
 export const ErrorBox=({message})=>h('div',{role:'alert'},message);
 export const Loading=()=>h('div',null,'loading');
 export const Modal=({title,children})=>h('section',{'aria-label':title},children);
 export const Table=({rows,columns})=>h('table',null,h('tbody',null,...rows.map((r)=>h('tr',{key:r.event_id},...columns.map((c)=>h('td',{key:c.key},c.render?c.render(r[c.key],r):r[c.key]))))));`);
const format = encoded(
  `export const num=(v,p=2)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(p):'—';export const dateTime=(v)=>v?String(v):'—';`,
);
const api = encoded(
  "export const api=()=>{throw Error('SSR must never fetch')};export const patch=api;export const post=api;export const useApi=()=>({});export const list=(v)=>Array.isArray(v)?v:Array.isArray(v?.items)?v.items:[];",
);
const lineUIURL = await compile("./WatchlistLineAlerts.tsx", {
  "../../components/CopyStockCode": await compile(
    "../../components/CopyStockCode.tsx",
    {
      "./stockCodeCopy": await compile("../../components/stockCodeCopy.ts"),
    },
  ),
  "./lineAlertPresentation": helpersURL,
  "../../components/UI": ui,
  "../../lib/format": format,
  "../../lib/api": api,
  "./WatchlistButton": encoded(
    `import {createElement as h} from ${JSON.stringify(import.meta.resolve("react"))};export default function Button(){return h('button',{'aria-label':'加入自选'},'加入自选');}`,
  ),
});
const {
  LineAlertEvidence,
  LineAlertBadge,
  default: Alerts,
} = await import(lineUIURL);
const html = renderToStaticMarkup(createElement(LineAlertEvidence, { event }));
assert.match(html, /23.2500/);
assert.match(html, /23.2000/);
assert.match(html, /23.6000/);
assert.match(html, /实际加入自选日.*2026-10-21/);
assert.match(html, /划线版本 V2/);
assert.match(html, /补跑历史触发/);
const converted = renderToStaticMarkup(
  createElement(LineAlertEvidence, {
    event: {
      ...event,
      line_price: 100,
      comparison_line_price: 50,
      price_basis: "forward_event_affine",
      anchor_date: "2026-10-15",
      comparison_evidence: { comparison_anchor: "2026-10-20" },
    },
  }),
);
assert.match(converted, /50.0000<small>判定线价/);
assert.match(converted, /原保存线价 100.0000/);
assert.match(converted, /锚定 2026-10-15/);
assert.match(converted, /判定价格锚点 2026-10-20/);
const risk = renderToStaticMarkup(
  createElement(LineAlertEvidence, { event: invalid }),
);
assert.match(risk, /line-alert-evidence-heading invalidated/);
assert.doesNotMatch(risk, /line-alert-evidence-heading triggered/);
assert.match(risk, /失守风险提醒/);
const frozenAutomatic = JSON.stringify(automatic);
const automaticHtml = renderToStaticMarkup(
  createElement(LineAlertEvidence, { event: automatic }),
);
assert.match(automaticHtml, /自动量价/);
assert.match(automaticHtml, /基准日期 2026-10-19/);
assert.match(automaticHtml, /配置版本 V3/);
assert.match(automaticHtml, /prior-frozen-source/);
assert.match(automaticHtml, /event-source/);
assert.match(automaticHtml, /止损位跌破/);
assert.match(automaticHtml, /不修改手动画线/);
assert.doesNotMatch(automaticHtml, /划线版本|原保存线价|补跑历史触发/);
assert.equal(
  JSON.stringify(automatic),
  frozenAutomatic,
  "render cannot recalculate or rewrite a frozen automatic event",
);
const breakoutHtml = renderToStaticMarkup(
  createElement(LineAlertEvidence, {
    event: {
      ...automatic,
      rule: "close_breakout",
      state: "triggered",
      volume_evidence: {
        ratio: 1.8,
        mean_start: "2026-09-21",
        mean_end: "2026-10-19",
      },
    },
  }),
);
assert.match(breakoutHtml, /突破日量能 1.80 倍前20日均量/);
assert.match(breakoutHtml, /不含突破当日/);
const missing = renderToStaticMarkup(
  createElement(LineAlertEvidence, {
    event: { ...event, previous_close: null },
  }),
);
assert.match(missing, /<dt>前收盘<\/dt><dd>—<\/dd>/);
assert.equal(
  renderToStaticMarkup(
    createElement(LineAlertBadge, {
      event,
      asOf: "2026-10-19",
      onOpen: () => {
        throw Error("render action");
      },
    }),
  ),
  '<span class="muted">—</span>',
);
const items = renderToStaticMarkup(
  createElement(Alerts, {
    data: { items: [event, invalid], total: 260, rules_total: 5, unknowns: [] },
    loading: false,
    error: "",
    asOf: "2026-10-21",
    onOpen: () => {},
    onStock: () => {},
    onReload: () => {
      throw Error("read render cannot evaluate");
    },
  }),
);
assert.match(items, /最近 2 \/ 260 条/);
assert.match(items, /回踩收回/);
assert.match(items, /支撑失守/);
assert.match(items, /查看详情/);
const automaticItems = renderToStaticMarkup(
  createElement(Alerts, {
    data: { items: [automatic], total: 1, rules_total: 1, unknowns: [] },
    loading: false,
    error: "",
    asOf: "2026-10-21",
    onOpen: () => {},
    onStock: () => {},
    onReload: () => {
      throw Error("render cannot evaluate");
    },
  }),
);
assert.match(automaticItems, /line-alert-source automatic/);
assert.match(automaticItems, /自动量价/);
assert.match(automaticItems, /止损位跌破/);
assert.match(automaticItems, /仅提醒 · 不自动入池/);
const annotationURL = await compile("../../components/chartAnnotations.ts");
const chartURL = await compile("../../components/ChartDrawingTools.tsx", {
  "../lib/api": api,
  "./UI": ui,
  "../lib/format": format,
  "./chartAnnotations": annotationURL,
  "./taskActivity": encoded("export const refreshTaskActivity=()=>{};"),
  "../lib/workbenchEvents": await compile("../../lib/workbenchEvents.ts"),
  "../features/watchlist/watchlistStore": encoded(
    "export const useWatchlistMembership=()=>({ids:new Set()});",
  ),
});
const { newLineSettings, savedLineSettings, projectChartLine } = await import(
  annotationURL
);
const { ChartAnnotationPanel } = await import(chartURL);
const nope = () => {
  throw Error("SSR must not mutate chart lines");
};
const state = {
  enabled: true,
  open: true,
  scope: { price_basis: "none", anchor_date: null },
  settings: newLineSettings(),
  isWatchlisted: false,
  show: true,
  drawing: false,
  price: "23.25",
  label: "",
  color: "blue",
  editing: null,
  busy: false,
  error: "",
  isIndex: false,
  lookback: 120,
  preview: null,
  selectedCandidates: [],
  defaults: newLineSettings,
  result: { loading: false, error: "", reload: nope },
  lines: [],
  views: [],
  setSettings: nope,
  setShow: nope,
  setOpen: nope,
  setDrawing: nope,
  setEditing: nope,
  setPrice: nope,
  setLabel: nope,
  setColor: nope,
  save: nope,
  mutate: nope,
  calculateLevels: nope,
  saveLevels: nope,
  setLookback: nope,
  setPreview: nope,
  setSelectedCandidates: nope,
};
const support = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, { state }),
);
assert.equal(
  (support.match(/checked=""/g) || []).length,
  4,
  "support radio, show marks, reclaim and auto-add enabled; breakdown is opt-in",
);
assert.match(support, /支撑失守提醒（风险）/);
assert.match(support, /保存后核验当前行情日，命中才加入/);
assert.match(support, /aria-label="支撑压力回看范围"/);
const quickDrawing = support.match(/<form class="chart-annotation-form">.*?<\/form>/)?.[0];
assert.ok(quickDrawing);
assert.match(quickDrawing, /水平线价格/);
assert.match(quickDrawing, /点图选价/);
assert.match(quickDrawing, /type="submit"[^>]*>保存划线/);
assert.doesNotMatch(
  quickDrawing,
  /<select|水平线名称|触发后自动加入自选|计算候选并预览/,
  "the drawing toolbar exposes only type, price and save controls",
);
assert.match(
  support,
  /<details class="chart-annotation-disclosure"><summary>提醒、备注与颜色<span>回踩收回 · 触发入自选/,
);
assert.match(support, /<details class="chart-annotation-disclosure chart-auto-levels">/);
assert.equal((support.match(/aria-label="选择.色线"/g) || []).length, 3);
assert.match(support, /划线与提醒/);
assert.doesNotMatch(support, /选择[蓝金]色线/);
for (const color of ["绿", "红", "紫"])
  assert.ok(support.includes(`选择${color}色线`));
const indexPanel = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, {
    state: {
      ...state,
      isIndex: true,
      settings: {
        line_type: "support",
        alert_modes: [],
        auto_watchlist: false,
      },
    },
  }),
);
assert.match(indexPanel, /指数划线与支撑压力/);
assert.match(indexPanel, /指数线仅保存与展示点位/);
assert.doesNotMatch(indexPanel, /触发后自动加入自选|支撑失守提醒（风险）/);
const candidatePanel = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, {
    state: {
      ...state,
      preview: {
        parameters: { as_of: "2026-10-23", period: "D", price_basis: "none" },
        reference_price: 12.98,
        reference_date: "2026-10-23",
        complete_bars: 120,
        notice: "保存不启用提醒或自动自选。",
        items: [
          {
            candidate_id: "candidate-1",
            line_type: "support",
            price: 12.1,
            touches: 2,
            last_pivot_date: "2026-09-28",
            confirmed_on: "2026-09-30",
            saved: false,
          },
        ],
      },
      selectedCandidates: ["candidate-1"],
    },
  }),
);
assert.match(candidatePanel, /参考收盘 12.9800/);
assert.match(candidatePanel, /2026-09-30确认/);
assert.match(candidatePanel, /保存所选支撑压力线（1）/);
assert.match(candidatePanel, /保存不启用提醒或自动自选/);
const legacy = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, {
    state: {
      ...state,
      settings: savedLineSettings({ label: "支撑位" }),
      lines: [
        {
          annotation_id: "old",
          instrument_id: "605089.SH",
          label: "支撑位",
          price: 23.25,
          color: "blue",
          price_basis: "none",
          anchor_date: null,
        },
      ],
      views: [
        projectChartLine(
          {
            annotation_id: "old",
            instrument_id: "605089.SH",
            label: "支撑位",
            price: 23.25,
            color: "blue",
            price_basis: "none",
            anchor_date: null,
          },
          { price_basis: "none", instrument: { instrument_id: "605089.SH" } },
        ),
      ],
    },
  }),
);
assert.doesNotMatch(legacy, /触发后自动加入自选/);
assert.match(legacy, /参考线/);
assert.match(legacy, /不提醒/);
const originalLine = {
  annotation_id: "old-anchor",
  instrument_id: "605089.SH",
  label: "原锚支撑",
  price: 12.5,
  color: "green",
  price_basis: "forward_event_affine",
  anchor_date: "2026-10-21",
  line_type: "support",
  alert_modes: ["support_reclaim"],
  auto_watchlist: true,
  revision: 2,
};
const projectedPanel = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, {
    state: {
      ...state,
      editing: originalLine,
      price: String(originalLine.price),
      lines: [originalLine],
      views: [
        {
          line: originalLine,
          price: 9.84,
          converted: true,
          anchor: "2026-10-23",
          reason: "按已核验公司行动换算",
        },
      ],
    },
  }),
);
assert.match(projectedPanel, /本图 9\.8400 · 已换算/);
assert.match(projectedPanel, /原保存线价 · 2026-10-21/);
assert.match(projectedPanel, /value="12.5"/);
assert.match(projectedPanel, /保存不更换原锚/);
assert.equal(originalLine.price, 12.5);
assert.equal(originalLine.anchor_date, "2026-10-21");
assert.match(projectedPanel, /未提醒，持续监控/);
assert.match(projectedPanel, /<details class="chart-annotation-disclosure chart-saved-lines">/);
const reminded = { ...originalLine, first_alert_date: "2026-10-22" };
const remindedPanel = renderToStaticMarkup(
  createElement(ChartAnnotationPanel, {
    state: {
      ...state,
      lines: [reminded],
      views: [{ line: reminded, price: 12.5 }],
    },
  }),
);
assert.match(remindedPanel, /首次提醒 2026-10-22，此后继续60个交易日/);
assert.doesNotMatch(remindedPanel, /未提醒，持续监控/);
const watchPresentation = await compile("./watchlistPresentation.ts");
const stockNavigation = await compile("../../components/stockNavigation.ts");
globalThis.__watchlistFixture = {
  data: null,
  loading: true,
  error: "",
  reload: nope,
};
const watchApi = encoded(
  "export const useApi=()=>globalThis.__watchlistFixture;export const list=(v)=>Array.isArray(v)?v:Array.isArray(v?.items)?v.items:[];",
);
const hotBrowseURL = await compile("../../components/HotBrowseFilter.tsx", {
  "../lib/api": watchApi,
  "./UI": ui,
  "../features/strategies/HotUniverse": await compile(
    "../strategies/HotUniverse.tsx",
  ),
  "./hotBrowsePresentation": await compile(
    "../../components/hotBrowsePresentation.ts",
  ),
});
const watchURL = await compile("./Watchlist.tsx", {
  "./lineAlertPresentation": helpersURL,
  "./lineAlertNotification": encoded(
    "export const LINE_ALERTS_OPEN='workbench-open-today-line-alerts';",
  ),
  "../../lib/api": watchApi,
  "../../components/UI": ui,
  "../../lib/format": encoded(
    "export const num=(v)=>String(v??'—');export const dateTime=()=>'';export const percent=(v)=>v==null?'—':String(v)+'%';export const tone=()=>'';",
  ),
  "../../components/stockNavigation": stockNavigation,
  "./WatchlistButton": encoded(
    "export default function Button(){return null;}",
  ),
  "./watchlistStore": encoded(
    "export const WATCHLIST_CHANGED='watchlist-test';",
  ),
  "../../components/DatePicker": encoded(
    "export default function DatePicker(){return null;}",
  ),
  "../../components/taskActivityPresentation": encoded(
    "export const activityPresentation=()=>null;",
  ),
  "./WatchlistLineAlerts": lineUIURL,
  "./useLineAlerts": encoded(
    "export const useLineAlerts=()=>globalThis.__watchlistAlertsFixture||({result:{data:null,error:'',loading:true,reload:()=>{}},evaluate:()=>{},busy:false,pending:false,task:null,error:''});",
  ),
  "./watchlistPresentation": watchPresentation,
  "../../components/HotBrowseFilter": hotBrowseURL,
});
const { default: Watchlist } = await import(watchURL);
const cold = renderToStaticMarkup(createElement(Watchlist, { onStock: nope }));
assert.match(cold, /我的自选池 <span>— 只<\/span>/);
assert.match(cold, /行情日 读取中/);
assert.doesNotMatch(cold, /待初始化|0 只|把值得关注/);
globalThis.__watchlistAlertsFixture = {
  result: {
    data: {
      rules_total: 4,
      latest_checks: {
        manual_line: {
          checked_rules: 2,
          trigger_count: 0,
          unknown_count: 0,
          is_current: true,
          checked_at_ms: 1,
        },
        automatic_price_volume: {
          checked_rules: 2,
          trigger_count: 0,
          unknown_count: 1,
          is_current: false,
          stale_reason: "自动配置已更新",
          checked_at_ms: 1,
        },
      },
    },
    error: "",
    loading: false,
    reload: nope,
  },
  evaluate: nope,
  busy: false,
  pending: false,
  task: null,
  error: "",
};
const independentChecks = renderToStaticMarkup(
  createElement(Watchlist, { onStock: nope }),
);
assert.match(independentChecks, /手动画线 · 最近核验 2 条/);
assert.match(independentChecks, /自动量价 · 需更新核验 2 条/);
assert.match(independentChecks, /自动配置已更新/);
console.log(
  "Line alerts: legacy quiet reference, support defaults, invalidation separation, historical isolation, frozen evidence and compact panel SSR passed",
);
