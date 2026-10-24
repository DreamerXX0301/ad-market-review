import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const compile = async (path) =>
  ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const helpers = encode(await compile("./dailyMainlinePresentation.ts"));
const ladder = encode(await compile("./ladderPresentation.ts"));
const ladderBoard = encode(
  (await compile("./ladderBoardPresentation.ts")).replaceAll(
    'from "./ladderPresentation"',
    "from " + JSON.stringify(ladder),
  ),
);
const board = encode((await compile("./boardPresentation.ts")).replaceAll('from "./ladderPresentation"', "from " + JSON.stringify(ladder)));
const boardComponent = encode((await compile("./BoardTags.tsx")).replaceAll('from "react/jsx-runtime"', "from " + JSON.stringify(runtime)).replaceAll('from "./boardPresentation"', "from " + JSON.stringify(board)).replace(/^import ["'][^"']+\.css["'];?$/gm, ""));
const ui = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Modal=({title,children})=>jsx('section',{children:[jsx('h2',{children:title},'title'),jsx('div',{children},'body')]});
export const Empty=({title,description})=>jsx('div',{children:[title,description]});
export const ErrorBox=({message})=>jsx('div',{role:'alert',children:message});export const Loading=()=>null;`);
const mockApi =
  encode(`let mainlineItems=[];export const setMainlineItems=(items)=>{mainlineItems=items};
export const useApi=(path)=>({data:path.includes('/history')?{items:[],total:0}:{date:'2026-01-05',items:mainlineItems,total:mainlineItems.length},loading:false,error:'',reload:()=>{}});
export const api=()=>{throw Error('render must not request or mutate')};export const post=api;`);
let compiled = await compile("./DailyMainlines.tsx");
for (const [name, replacement] of Object.entries({
  "react/jsx-runtime": runtime,
  react: import.meta.resolve("react"),
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../lib/api": mockApi,
  "../../lib/format": encode(
    `export const dateTime=value=>String(value);export const percent=value=>value==null?'—':value+'%';export const tone=()=>'';`,
  ),
  "../../components/UI": ui,
  "./dailyMainlinePresentation": helpers,
  "./ladderBoardPresentation": ladderBoard,
  "./ladderPresentation": encode(await compile("./ladderPresentation.ts")),
  "./BoardTags": boardComponent,
  "../../components/CopyStockCode": encode(
    `import {jsx} from ${JSON.stringify(runtime)};export default ()=>jsx('span',{children:'复制代码'});`,
  ),
}))
  compiled = compiled.replaceAll(
    JSON.stringify(name),
    JSON.stringify(replacement),
  );
const {
  default: DailyMainlines,
  MainlineEvidence,
  MainlineEditor,
} = await import(
  encode(compiled.replace('import "./DailyMainlines.css";', ""))
);
const { emptyMainlineAttribution } = await import(helpers);
const { setMainlineItems } = await import(mockApi);
const stock = {
  instrument_id: "600001.SH",
  name: "合成测试证券",
  reason: "原因线索不生成主线",
  topics: [{ name: "热门概念" }],
};
let html = renderToStaticMarkup(
  createElement(DailyMainlines, {
    date: "2026-01-05",
    rows: [stock],
    onStock: () => {},
  }),
);
assert.match(html, /当日主线复盘/);
assert.match(html, /未归因/);
assert.match(html, /有据录入主线/);
assert.match(html, /尚无当日主线证据，行业和概念不会代填。/);
assert.doesNotMatch(
  html,
  /热门概念/,
  "a concept is not silently converted into a daily group",
);
const initialProps = {
  date: "2026-01-05",
  rows: [stock],
  onStock: () => {},
};
for (const reason of [undefined, null, "", " \t ", " - ", " —–- "]) {
  html = renderToStaticMarkup(
    createElement(DailyMainlines, {
      ...initialProps,
      rows: [{ ...stock, reason }],
    }),
  );
  assert.match(
    html,
    /来源原因：待核验/,
    "blank and dash-only source reasons remain unknown",
  );
}
const rawReasonStock = {
  ...stock,
  reason: "  原因甲 + 原因乙—后续（来源原文）  ",
};
html = renderToStaticMarkup(
  createElement(DailyMainlines, { ...initialProps, rows: [rawReasonStock] }),
);
assert.match(html, /来源原因：原因甲 \+ 原因乙—后续（来源原文）/);
assert.equal(
  rawReasonStock.reason,
  "  原因甲 + 原因乙—后续（来源原文）  ",
  "display normalization never rewrites the input source text",
);
const searchableRows = [
  stock,
  { ...stock, instrument_id: "600002.SH", name: "另一只合成测试证券" },
];
html = renderToStaticMarkup(
  createElement(DailyMainlines, {
    ...initialProps,
    rows: searchableRows,
    query: "没有匹配的关键词",
  }),
);
assert.match(html, /没有匹配的当日主线样本/);
assert.match(html, /已返回确认涨停样本 <b>2<\/b>/);
assert.doesNotMatch(html, /class="daily-mainline-card"/);
assert.doesNotMatch(html, /当前没有可展示的确认涨停样本/);
html = renderToStaticMarkup(
  createElement(DailyMainlines, {
    ...initialProps,
    rows: searchableRows,
    query: "600002.SH",
  }),
);
assert.doesNotMatch(html, /没有匹配的当日主线样本/);
assert.match(html, /另一只合成测试证券/);
assert.match(html, /已返回确认涨停样本 <b>2<\/b>/);
setMainlineItems([{
  date: initialProps.date,
  instrument_id: stock.instrument_id,
  instrument_name: stock.name,
  revision: 2,
  revision_id: "synthetic-revoke-revision",
  action: "revoke",
  source_kind: "manual_user_entry",
  recorded_at_ms: 1,
  change_reason: "合成撤销原因",
  attributions: [],
  display_state: "unattributed",
  display_mainline: null,
  secondary_mainlines: [],
}]);
html = renderToStaticMarkup(createElement(DailyMainlines, initialProps));
assert.match(html, /当前当日主线归属已撤销；历史证据与修订可在下方审计中查阅。/);
assert.match(html, /更正 \/ 撤销 \/ 审计/);
assert.doesNotMatch(html, /尚无当日主线证据/);
setMainlineItems([]);
const shapeStock = {
  ...stock,
  date: "2026-01-05",
  snapshot_id: "synthetic-generation",
  pattern_date: "2026-01-05",
  pattern_as_of: "2026-01-05",
  pattern_status: { earth_to_sky: { state: true, status: "confirmed" } },
  pattern_events: [{
    kind: "earth_to_sky", date: "2026-01-05", bar_date: "2026-01-05",
    snapshot_id: "synthetic-generation", basis: "verified-source-close-pool-and-raw-ohlc-v1",
    raw_price_basis: "none", raw_volume: 100,
    raw_ohlc: { open: 9, high: 11, low: 9, close: 11 }, source: "synthetic-source",
  }],
};
const shapeProps = { date: "2026-01-05", rows: [shapeStock], snapshotId: "synthetic-generation", onStock: () => {} };
html = renderToStaticMarkup(createElement(DailyMainlines, shapeProps));
assert.match(html, /data-board-tag="type:earth_to_sky"/);
html = renderToStaticMarkup(createElement(DailyMainlines, { ...shapeProps, snapshotId: "another-generation" }));
assert.doesNotMatch(html, /data-board-tag="type:/);
const item = {
  ...emptyMainlineAttribution(),
  mainline_name: "测试归属",
  evidence_ref: "javascript:alert(1)",
  source_name: "合成来源",
  source_reason: "&lt;原文&gt;",
  role: "core_candidate",
};
html = renderToStaticMarkup(createElement(MainlineEvidence, { item }));
assert.doesNotMatch(html, /href="javascript:/);
assert.match(html, /核心候选（用户标记）/);
assert.match(html, /用户手工录入/);
assert.match(html, /来源发布时间/);
assert.match(html, /未知/);
assert.match(
  html,
  /&amp;lt;原文&amp;gt;/,
  "source text is escaped, never interpreted as markup",
);
html = renderToStaticMarkup(
  createElement(MainlineEditor, {
    date: "2026-01-05",
    stock,
    onSaved: () => {},
    onClose: () => {},
  }),
);
for (const label of [
  "证据引用",
  "来源原因原文",
  "来源发布时间",
  "证据 SHA256",
  "本次录入 / 更正 / 撤销理由",
  "主组待定",
  "系统实际记录时间不可回填",
])
  assert.ok(html.includes(label), label);
assert.match(
  html,
  /value="unverified" selected=""/,
  "manual draft starts unverified",
);
assert.doesNotMatch(
  html,
  /name="(?:recorded_at_ms|first_known_at_ms)"/,
  "users cannot backdate audit timestamps",
);
assert.match(
  html,
  /当日主线名称<input[^>]*value=""/,
  "new manual entry has no default mainline seed",
);
console.log(
  "daily mainline source reasons, search states, revocation, manual evidence and audit form passed",
);
