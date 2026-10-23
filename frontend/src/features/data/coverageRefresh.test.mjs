/** Actual CoveragePanel JSX + real React SSR: a background refresh retains controllers. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const react = import.meta.resolve("react"),
  runtime = import.meta.resolve("react/jsx-runtime");
const ui = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Panel=({children})=>jsx('section',{children});
export const Modal=({children,title})=>jsx('section',{'aria-label':title,children});
export const Badge=({children})=>jsx('span',{children});
export const Empty=({title})=>jsx('p',{children:title});
export const ErrorBox=({message})=>jsx('p',{role:'alert',children:message});
export const Loading=()=>jsx('div',{'data-first-load':'true',children:'initial-loading'});
export const Spinner=()=>null;`);
globalThis.__coverageDetails = {
  data: null,
  loading: true,
  error: "",
  reload() {
    globalThis.__coverageDetails.loading = true;
    globalThis.__coverageReloads++;
  },
};
globalThis.__coverageReloads = 0;
globalThis.__sourceRenderCount = 0;
const api =
  encode(`export const useApi=(path)=>path==='/data/coverage/check'?globalThis.__coverageDetails:{data:null,loading:false,error:'',reload:()=>{}};
export const post=()=>{throw Error('SSR must not create a task')};export const requestKey=()=> 'fixture';`);
async function compile(
  path,
  replacements = {},
  transform = (source) => source,
) {
  const raw = transform(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
  );
  let source = ts.transpileModule(raw, {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const [name, value] of Object.entries({
    react,
    "react/jsx-runtime": runtime,
    "lucide-react": import.meta.resolve("lucide-react"),
    "../../lib/api": api,
    "../../components/UI": ui,
    "../../lib/format": encode(
      "export const integer=(v)=>v==null?'—':String(v);export const dateTime=(v)=>String(v);",
    ),
    "../../components/CopyStockCode": encode(
      "export default function Copy(){return null;}",
    ),
    ...replacements,
  }))
    source = source.replaceAll(JSON.stringify(name), JSON.stringify(value));
  return encode(source.replace(/import "[^\"]+\.css";/g, ""));
}
const helpers = await compile("./dataPresentation.ts");
const gap = await compile("./GapDiagnosisPanel.tsx", {
  "./dataPresentation": helpers,
});
const listing = await compile("./ListingIdentityPanel.tsx", {
  "./dataPresentation": helpers,
});
const source = await compile("./GapSourceCheck.tsx", {
  "../../components/taskActivity": encode(
    "export const useTaskActivity=()=>({tasks:[],pending:[]});export const startTaskRequest=()=>{};export const finishTaskRequest=()=>{};",
  ),
  "../../components/taskActivityPresentation": encode(
    "export const activityPresentation=()=>({});",
  ),
});
const sourceProbe =
  encode(`import {createElement} from ${JSON.stringify(react)};import Source from ${JSON.stringify(source)};
export default function Probe(props){globalThis.__sourceRenderCount++;globalThis.__sourceCompletionProps=props;return createElement(Source,props);}`);
const parent = await compile(
  "./CoveragePanel.tsx",
  {
    "./dataPresentation": helpers,
    "./GapDiagnosisPanel": gap,
    "./ListingIdentityPanel": listing,
    "./GapSourceCheck": sourceProbe,
  },
  (raw) => {
    // Inject only the user's already-open modal state. Every condition and child
    // component remains the real product source and is rendered by real React.
    assert.match(raw, /useState<Row \| null>\(null\)/);
    return raw.replace(
      "useState<Row | null>(null)",
      "useState<Row | null>(globalThis.__openedCoverageFixture)",
    );
  },
);
const { default: CoveragePanel } = await import(parent);
const props = {
  datasets: [],
  audit: { available: true, plan_id: "current-plan" },
  loading: false,
  disabled: false,
  remoteConfigured: true,
  onReload() {},
  onTask() {
    throw Error("render cannot launch work");
  },
};
const report = {
  plan_id: "current-plan",
  stale: false,
  report: {
    daily: {
      ready: true,
      gap_diagnosis: {
        diagnosis_id: "diag",
        total_missing_sessions: 7932,
        stock_count: 1125,
        episode_count: 1847,
        isolated_episode_count: 897,
        long_episode_count: 729,
        details_complete: true,
        stocks: [],
      },
    },
  },
};
for (const module of ["quote_gaps", "daily_raw", "analysis_bars"]) {
  globalThis.__openedCoverageFixture = { module, name: "共用缺口诊断" };
  globalThis.__coverageDetails = {
    ...globalThis.__coverageDetails,
    data: null,
    loading: true,
  };
  const first = renderToStaticMarkup(createElement(CoveragePanel, props));
  assert.match(
    first,
    /data-first-load="true"/,
    "only an initial read with no retained data may replace the dialog with Loading",
  );
  assert.doesNotMatch(first, /coverage-source-check|coverage-gap-diagnosis/);
  globalThis.__coverageDetails = {
    ...globalThis.__coverageDetails,
    data: report,
    loading: false,
  };
  const ready = renderToStaticMarkup(createElement(CoveragePanel, props));
  assert.match(ready, /coverage-source-check/);
  assert.match(ready, /coverage-gap-diagnosis/);
  const beforeReloads = globalThis.__coverageReloads,
    beforeRenders = globalThis.__sourceRenderCount;
  globalThis.__sourceCompletionProps.onChanged();
  assert.equal(
    globalThis.__coverageReloads,
    beforeReloads + 1,
    "invoke the actual parent completion callback that starts details.reload",
  );
  assert.equal(globalThis.__coverageDetails.loading, true);
  const refreshing = renderToStaticMarkup(createElement(CoveragePanel, props));
  assert.match(
    refreshing,
    /coverage-source-check/,
    "a completed source task refresh must retain its controller, so its seen-completion ref is not discarded",
  );
  assert.match(
    refreshing,
    /coverage-gap-diagnosis/,
    "cached diagnosis stays readable during the refresh",
  );
  assert.doesNotMatch(
    refreshing,
    /data-first-load="true"/,
    "cached data must not take the first-load branch",
  );
  assert.equal(
    globalThis.__sourceRenderCount,
    beforeRenders + 1,
    "the actual source controller remains in the rendered subtree",
  );
  globalThis.__coverageDetails.loading = false;
  const settled = renderToStaticMarkup(createElement(CoveragePanel, props));
  assert.match(settled, /coverage-source-check/);
  assert.match(settled, /coverage-gap-diagnosis/);
}
console.log(
  "Coverage refresh regression: real React parent callback preserves source controller and shared diagnosis with retained data while loading; first load still shows Loading",
);
