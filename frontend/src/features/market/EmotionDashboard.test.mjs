import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const helper = ts.transpileModule(
  await fs.readFile(
    new URL("./sentimentPresentation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
let code = ts.transpileModule(
  await fs.readFile(new URL("./EmotionDashboard.tsx", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
).outputText;
const modules = {
  react: import.meta.resolve("react"),
  "react/jsx-runtime": runtime,
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../components/UI": encode(
    `import {jsx} from ${JSON.stringify(runtime)};export function Badge({children}){return jsx('span',{className:'badge',children});}`,
  ),
  "./MetricHelp": encode(
    `import {jsx} from ${JSON.stringify(runtime)};export function MetricHelp({description}){return jsx('span',{title:description,children:'ⓘ'});}`,
  ),
  "./MarketDataCoverage": encode(
    "export default function Coverage(){return null;}",
  ),
  "./SentimentSnapshots": encode(
    "export default function Snapshots(){return null;}",
  ),
  "./sentimentPresentation": encode(helper),
};
for (const [name, url] of Object.entries(modules))
  code = code.replaceAll('from "' + name + '"', "from " + JSON.stringify(url));
code = code.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const { default: Dashboard } = await import(encode(code));
const metric = (key, value, label = key) => ({ key, label, value, unit: "%" });
const sentiment = {
  date: "2026-10-21",
  index_environment: {
    indices: [
      {
        instrument_id: "000001.SH",
        name: "上证指数",
        date: "2026-10-21",
        observed_count: 21,
        sample_count: 21,
        coverage_pct: 100,
        state_label: "指数偏弱",
        metrics: [metric("index_20d_return_pct", -3.9, "20日涨幅")],
      },
    ],
  },
  market: {
    state_label: "样本普遍回落",
    metrics: [metric("median_return_pct", -1.2)],
    sample_count: 100,
    observed_count: 100,
    coverage_pct: 100,
    unknown_count: 0,
  },
  short_term: {
    state_label: "全部样本偏强",
    sample_count: 52, observed_count: 54, unknown_count: 2, coverage_pct: 96.29, promotion_bounds_pct: { lower: 24, upper: 28 }, cohort_source_complete: true,
    metrics: [metric("previous_limit_up_avg_return_pct", 3, "全体价格均值")],
    groups: [
      {
        id: "ordinary_10",
        label: "普通10%已核验组",
        state_label: "样本有限",
        metrics: [
          metric("previous_limit_up_avg_return_pct", 10, "同制度价格均值"),
        ],
        sample_count: 16,
        observed_count: 46,
        current_identity_unknown_count: 30,
        promotion_bounds_pct: { lower: 34.78, upper: 100 },
        cohort_source_complete: true,
      },
    ],
  },
  provenance: {
    mode: "retained_retrospective",
    rule_version: "context-v1",
    input_snapshot_ids: ["snapshot-A"],
    input_fingerprint: "abc123",
    first_known_at: null,
    source_records: [
      {
        trade_date: "2026-10-20",
        source: "source-A",
        pool: "up",
        fetched_at: "2026-10-21T00:00:00Z",
        first_known_at: null,
      },
    ],
  },
};
const before = structuredClone(sentiment);
const markup = renderToStaticMarkup(
  createElement(Dashboard, {
    sentiment,
    observation: createElement("p", null, "旧版广度"),
  }),
);
for (const text of [
  "指数环境",
  "个股广度",
  "短线接力",
  "上证指数",
  "-3.90",
  "普通10%已核验组",
  "24%—28%",
  "当前仅52 / 54只具有可比反馈",
  "当前留存回看",
  "snapshot-A",
  "abc123",
  "2026/10/21 08:00:00 北京时间",
  "不是六阶段情绪周期的确认结果",
])
  assert.ok(markup.includes(text), text);
assert.match(
  markup,
  /<dt>历史首次可知<\/dt><dd>未登记<\/dd>/,
  "fetch time must not become historical first-known time",
);
assert.ok(markup.includes("全体价格均值"));
const visibleGroup = markup.match(/<section class="sentiment-dimension sentiment-dimension--short_term">([\s\S]*?)<\/section>/)?.[1];
assert.ok(visibleGroup && !visibleGroup.includes("同制度价格均值"), "ordinary-only survivor feedback does not replace all-price observation by default");
assert.match(markup, /aria-pressed="true">全部队列价格观察/);
assert.deepEqual(sentiment, before);
const missing = renderToStaticMarkup(
  createElement(Dashboard, { sentiment: {} }),
);
assert.ok(missing.includes("尚无可核验的指数窗口"));
assert.ok(missing.includes("历史首次可知"));
assert.ok(!missing.includes("NaN"));
console.log(
  "emotion dashboard: separate E/B/S, default all-queue price observation, identity gaps and non-backfilled provenance passed",
);
