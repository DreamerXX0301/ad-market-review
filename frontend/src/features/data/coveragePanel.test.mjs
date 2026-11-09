import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
async function compiled(name, jsx = false) {
  return ts.transpileModule(
    await fs.readFile(new URL(name, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}),
      },
    },
  ).outputText;
}
const helpers = encode(await compiled("./dataPresentation.ts"));
const ui = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Panel=({title,subtitle,actions,children})=>jsx('section',{children:[jsx('h2',{children:title},'title'),jsx('p',{children:subtitle},'subtitle'),jsx('div',{children:actions},'actions'),jsx('div',{children},'body')]});
export const Badge=({children})=>jsx('span',{children});
export const Empty=({title,description})=>jsx('div',{children:[title,description]});
export const ErrorBox=({message})=>jsx('div',{role:'alert',children:message});
export const Loading=()=>null;export const Spinner=()=>null;export const Modal=()=>null;`);
const api = encode(
  `export const useApi=()=>({data:null,loading:false,error:null});export const post=()=>{throw Error('render must not create tasks')};export const requestKey=()=> 'fixture';`,
);
const format = encode(
  `export const integer=value=>value==null?'—':Number(value).toLocaleString('en-US');export const dateTime=value=>String(value);`,
);
async function loadComponent(path, replacements = {}) {
  let source = await compiled(path, true);
  for (const [name, target] of Object.entries({
    "react/jsx-runtime": runtime,
    react: import.meta.resolve("react"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "../../lib/api": api,
    "../../lib/format": format,
    "../../components/UI": ui,
    "./dataPresentation": helpers,
    "../../components/CopyStockCode": encode(
      `import {jsx} from ${JSON.stringify(runtime)};export default function Copy({code,instrumentId}){return jsx('button',{'aria-label':'复制代码',children:code||instrumentId});}`,
    ),
    ...replacements,
  }))
    source = source.replaceAll(JSON.stringify(name), JSON.stringify(target));
  return encode(source.replace(/import "[^\"]+\.css";/g, ""));
}
const listingComponent = await loadComponent("./ListingIdentityPanel.tsx");
const gapComponent = await loadComponent("./GapDiagnosisPanel.tsx");
const sourceComponent = await loadComponent("./GapSourceCheck.tsx", {
  "../../components/taskActivity": encode(
    "export const useTaskActivity=()=>({tasks:[],pending:[]});export const startTaskRequest=()=>{};export const finishTaskRequest=()=>{};",
  ),
  "../../components/taskActivityPresentation": encode(
    "export const activityPresentation=()=>({});",
  ),
});
let component = await compiled("./CoveragePanel.tsx", true);
component = component
  .replaceAll('"react/jsx-runtime"', JSON.stringify(runtime))
  .replaceAll('"react"', JSON.stringify(import.meta.resolve("react")))
  .replaceAll(
    '"lucide-react"',
    JSON.stringify(import.meta.resolve("lucide-react")),
  )
  .replaceAll('"../../lib/api"', JSON.stringify(api))
  .replaceAll('"../../lib/format"', JSON.stringify(format))
  .replaceAll('"../../components/UI"', JSON.stringify(ui))
  .replaceAll('"./dataPresentation"', JSON.stringify(helpers))
  .replaceAll('"./ListingIdentityPanel"', JSON.stringify(listingComponent))
  .replaceAll('"./GapDiagnosisPanel"', JSON.stringify(gapComponent))
  .replaceAll('"./GapSourceCheck"', JSON.stringify(sourceComponent))
  .replace('import "./CoveragePanel.css";', "");
const {
  default: CoveragePanel,
  CorporateActionScope,
  corporateActionSummary,
} = await import(encode(component));
const datasets = [
  "calendar",
  "security_identity",
  "daily_raw",
  "corporate_actions",
  "analysis_bars",
  "indicators",
  "topics",
  "market_review",
].map((module) => ({
  module,
  name: module,
  row_count: module === "security_identity" ? 5584 : 100,
  first_date: "2021-10-20",
  last_date: "2026-10-21",
  coverage_progress: {
    label: module === "corporate_actions" ? "已留存公司行动" : "统计范围",
    observed_count: module === "security_identity" ? 5576 : null,
    expected_count: module === "security_identity" ? 5580 : null,
    unit: "只",
    state: module === "security_identity" ? "known" : "unknown",
    coverage_status:
      module === "indicators"
        ? "on_demand"
        : module === "security_identity"
          ? "partial"
          : "unknown",
    reason:
      module === "corporate_actions"
        ? "来源未提供全市场应有事件总量，无法计算完整率"
        : "需要明确范围",
  },
}));
const props = {
  datasets,
  audit: { available: true, plan_id: "fixture" },
  loading: false,
  disabled: false,
  remoteConfigured: false,
  onReload() {},
  onTask() {
    throw Error("render must not create task");
  },
};
const markup = renderToStaticMarkup(createElement(CoveragePanel, props));
assert.equal(
  (markup.match(/<tr(?:\s[^>]*)?>/g) || []).length,
  9,
  "eight compact module rows plus one header",
);
assert.match(markup, /核验本地数据/);
assert.match(markup, /5,576 \/ 5,580/);
assert.doesNotMatch(
  markup,
  />5,584</,
  "stock row does not advertise an index-inclusive denominator",
);
assert.match(markup, /来源未提供全市场应有事件总量/);
assert.match(markup, /日 \/ 周 \/ 月/);
assert.doesNotMatch(
  markup,
  /已覆盖.*—|应覆盖.*—|>—</,
  "unknown totals use a reason, not empty fake fractions",
);
assert.match(markup, /尚未运行本地核验/);
const listings = {
  catalog_count: 5580,
  stock_count: 5580,
  listed_count: 5575,
  known_count: 5575,
  pending_listing_count: 5,
  unknown_count: 0,
};
const { ListingIdentitySummary, ListingIdentityRows } = await import(
  listingComponent
);
const identitySummary = renderToStaticMarkup(
  createElement(ListingIdentitySummary, { identities: listings }),
);
assert.match(identitySummary, /5,580/);
assert.match(identitySummary, /5,575<span> \/ 5,575/);
assert.match(identitySummary, /100%/);
const updatedDatasets = datasets.map((row) =>
  row.module === "security_identity"
    ? {
        ...row,
        coverage_progress: {
          ...row.coverage_progress,
          observed_count: 5575,
          expected_count: 5575,
          catalog_count: 5580,
          pending_listing_count: 5,
          unknown_count: 0,
        },
      }
    : row,
);
const updated = renderToStaticMarkup(
  createElement(CoveragePanel, { ...props, datasets: updatedDatasets }),
);
assert.match(
  updated,
  />5,580<\/b>/,
  "catalog total is independent from the current listed denominator",
);
assert.match(updated, /5,575 \/ 5,575/);
assert.match(updated, /待上市 5 只/);
const scopedCoverage = renderToStaticMarkup(
  createElement(CoveragePanel, {
    ...props,
    datasets: [
      ["market_review", 74, 74, "known"],
      ["security_identity", 5575, 5575, "known"],
      ["topics", 710, 710, "known"],
      ["calendar", 9999999, 10000000, "known"],
      ["daily_raw", 100, 100, "unknown"],
    ].map(([module, observed, expected, state]) => ({
      module,
      name: module,
      status: "partial",
      coverage_progress: {
        state,
        observed_count: observed,
        expected_count: expected,
        // A claimed or rounded 100% must not override the validated counts/state.
        percent: 100,
        coverage_status: "covered",
        reason: "当前维度不证明来源完整历史",
      },
    })),
  }),
);
assert.equal(
  (scopedCoverage.match(/class="coverage-measure-covered"/g) || []).length,
  3,
  "only exact counts with known coverage receive the completed style",
);
assert.equal((scopedCoverage.match(/当前范围已覆盖/g) || []).length, 3);
assert.match(
  scopedCoverage,
  /<small class="coverage-complete-state"><svg[^>]*aria-hidden="true"/,
  "completed coverage includes an icon and a visible scope label",
);
assert.match(scopedCoverage, /&lt;100%/);
assert.match(scopedCoverage, /当前维度不证明来源完整历史/);
const unknownMarkup = renderToStaticMarkup(
  createElement(ListingIdentityRows, {
    rows: [
      {
        instrument_id: "000001.SZ",
        name: "测试证券",
        listing_status: "unknown",
        evidence_kind: "unverified",
        reason: "来源缺少日期，尚未确认",
        can_edit: true,
        reference: {
          quote_presence: { valid_quote_count: 0 },
          manual_confirmation: { confirmation_id: "c", evidence: "人工待核验" },
        },
      },
    ],
    onEdit() {
      throw Error("render cannot confirm");
    },
  }),
);
assert.match(unknownMarkup, /身份待核验/);
assert.match(unknownMarkup, /本代有效成交报价 0 条/);
assert.doesNotMatch(
  unknownMarkup,
  /\[object Object\]|>待上市<\/span>/,
  "missing dates or no quotes do not classify unknown as pending",
);
const { GapStock, GapEpisode, default: GapPanel } = await import(gapComponent);
const episode = {
  episode_id: "g1",
  start_date: "2026-10-15",
  end_date: "2026-10-21",
  missing_sessions: 5,
  dates: ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"],
  shape: "long_consecutive",
  shape_label: "较长连续区间",
  diagnosis_type: "source_unquoted_unknown",
  type_label: "原始包同样无报价",
  reason: "本地来源包也没有这些日期，原因尚未确认",
  impact: "严格日线指标与收益保留缺口",
  next_action: "向来源核验后，再按明确证据处理",
  source_comparison: {
    raw_available_sessions: 0,
    raw_absent_sessions: 5,
    basis: "本地原始包逐日比对",
  },
  confirmed_suspension: null,
};
const stock = {
  instrument_id: "000001.SZ",
  name: "测试股票",
  missing_sessions: 5,
  episode_count: 1,
  longest_episode_sessions: 5,
  episodes: [episode],
};
const stockMarkup = renderToStaticMarkup(createElement(GapStock, { stock }));
assert.match(stockMarkup, /结论/);
assert.match(stockMarkup, /影响/);
assert.match(stockMarkup, /下一步/);
assert.match(stockMarkup, /原因尚未确认/);
assert.doesNotMatch(
  stockMarkup,
  /<time|coverage-gap-episode/,
  "collapsed stock rows do not mount thousands of interval/date nodes",
);
const episodeMarkup = renderToStaticMarkup(
  createElement(GapEpisode, { episode }),
);
assert.doesNotMatch(
  episodeMarkup,
  /<time/,
  "date details are lazy until explicitly expanded",
);
assert.match(episodeMarkup, /形态不代表停牌原因/);
const sourceEpisode = renderToStaticMarkup(
  createElement(GapEpisode, {
    episode: {
      ...episode,
      source_verification: {
        status: "source_no_quote",
        reason: "本次来源同样未返回报价，不能证明停牌",
        next_action: "保留无报价结论，需公告才能确定原因",
        checked_at_ms: 123,
        source: "test",
        returned_dates: [],
        missing_dates: episode.dates,
        local_difference_dates: [],
      },
    },
  }),
);
assert.match(sourceEpisode, /来源同样无报价/);
assert.match(sourceEpisode, /不能证明停牌/);
assert.match(sourceEpisode, /仍无报价 5 日/);
const diagnosis = {
  total_missing_sessions: 5,
  stock_count: 1,
  episode_count: 1,
  isolated_episode_count: 0,
  long_episode_count: 1,
  details_complete: true,
  stocks: [stock],
};
const gapMarkup = renderToStaticMarkup(
  createElement(GapPanel, { daily: { ready: true, gap_diagnosis: diagnosis } }),
);
assert.match(gapMarkup, /两个模块引用同一份结果/);
assert.match(gapMarkup, /原因尚未确认/);
assert.doesNotMatch(gapMarkup, /<time/);
const actionScope = {
  window_ready: true,
  window_rows: 21526,
  window_begin: "2021-10-20",
  window_end: "2026-10-21",
  ready: false,
  file_rows: 57715,
  file_invalid_rows: 2,
  outside_window_special_rows: 2,
  special_items: [
    {
      code: "000887",
      date: "2007-03-30",
      scope: "outside_window",
      reason: "特殊负送转需单独核验",
    },
  ],
};
assert.match(
  corporateActionSummary(actionScope),
  /本窗口21,526条字段通过；另有窗口外2条特殊行动需单独核验/,
);
const actionMarkup = renderToStaticMarkup(
  createElement(CorporateActionScope, { actions: actionScope }),
);
assert.match(actionMarkup, /窗口内结构核验/);
assert.match(actionMarkup, /来源全文件特殊行动/);
assert.match(actionMarkup, /2007-03-30/);
assert.match(actionMarkup, /本窗口之外/);
assert.doesNotMatch(actionMarkup, /来源错误|文件损坏|尚未核验|100%/);
console.log(
  "Coverage panel: compact eight-row layout, stock-only denominator, explicit unknown totals and manual local verification passed",
);
