import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, tsx = false) =>
  ts
    .transpileModule(source, {
      fileName: tsx ? "component.tsx" : "module.ts",
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    .outputText.replaceAll(
      '"react/jsx-runtime"',
      JSON.stringify(import.meta.resolve("react/jsx-runtime")),
    );
const helper = encode(
  compile(
    await fs.readFile(
      new URL("./researchEvidencePresentation.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const {
  researchUrl,
  researchEvidenceRefs,
  researchClaim,
  researchRows,
  researchEvidencePage,
  researchEvidenceKind,
  groupResearchQuestions,
} = await import(helper);
for (const url of [
  "javascript:alert(1)",
  "http://example.com",
  "https://user:pass@example.com",
  "/local",
  null,
])
  assert.equal(researchUrl(url), null);
assert.equal(
  researchUrl("https://example.com/report"),
  "https://example.com/report",
);
assert.deepEqual(researchRows([null, false, "text", [], { title: "valid" }]), [
  { title: "valid" },
]);
assert.deepEqual(
  researchEvidenceRefs(
    ["e2", "missing", "e2"],
    [{ evidence_id: "e1" }, { evidence_id: "e2" }],
  ),
  [{ id: "e2", index: 1 }],
);
assert.equal(researchClaim("title_clue"), "标题线索");
const indicator = encode(
  compile(
    await fs.readFile(
      new URL("../../lib/indicatorVisibility.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const format = encode(
  compile(
    (
      await fs.readFile(new URL("../../lib/format.ts", import.meta.url), "utf8")
    ).replace('"./indicatorVisibility"', JSON.stringify(indicator)),
  ),
);
const financialComparison = encode(
  compile(
    await fs.readFile(
      new URL("./researchFinancialComparison.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const financialValue = encode(
  compile(
    (
      await fs.readFile(
        new URL("./ResearchFinancialValue.tsx", import.meta.url),
        "utf8",
      )
    )
      .replace('"../../lib/format"', JSON.stringify(format))
      .replace(
        '"./researchFinancialComparison"',
        JSON.stringify(financialComparison),
      ),
    true,
  ),
);
const react = JSON.stringify(import.meta.resolve("react"));
// A small state harness exercises the actual reader callbacks without a browser.
// React still renders the resulting element tree through its real server renderer.
const hooksModule = encode(`let slots=[], cursor=0, effects=[];
export function begin(){cursor=0;effects=[];}
export function reset(){slots=[];begin();}
export function flush(){for(const effect of effects)effect();effects=[];}
export function useState(initial){const slot=cursor++;if(!(slot in slots))slots[slot]=typeof initial==='function'?initial():initial;return [slots[slot],value=>{slots[slot]=typeof value==='function'?value(slots[slot]):value;}];}
export function useRef(initial){return useState({current:initial})[0];}
export function useEffect(callback){effects.push(callback);}
`);
const hooks = await import(hooksModule);
const ui = encode(
  `import React from ${react}; export const Badge=({children})=>React.createElement('span',null,children); export const Panel=({title,subtitle,children})=>React.createElement('section',null,React.createElement('h2',null,title),React.createElement('p',null,subtitle),children); export const Table=({rows=[],columns=[],emptyTitle})=>React.createElement('div',null,rows.length?rows.map((row,i)=>React.createElement('div',{key:i},columns.map((col,j)=>React.createElement('span',{key:j},col.render?col.render(row[col.key],row,i):String(row[col.key]??'—'))))):emptyTitle);`,
);
const charts = encode(
  `import React from ${react}; export function CandleChart(props){globalThis.__researchFrozenProps=props;return React.createElement('div',{'data-frozen-chart':true});}`,
);
const copy = encode(
  `import React from ${react}; export default ({instrumentId})=>React.createElement('code',null,instrumentId);`,
);
const source = (
  await fs.readFile(
    new URL("./ResearchEvidenceDetails.tsx", import.meta.url),
    "utf8",
  )
)
  .replace('"react"', JSON.stringify(hooksModule))
  .replace('"../../lib/format"', JSON.stringify(format))
  .replace('"../../components/UI"', JSON.stringify(ui))
  .replace('"../../components/Charts"', JSON.stringify(charts))
  .replace('"../../components/CopyStockCode"', JSON.stringify(copy))
  .replace('"./ResearchFinancialValue"', JSON.stringify(financialValue))
  .replace('"./researchEvidencePresentation"', JSON.stringify(helper))
  .replace(
    '"lucide-react"',
    JSON.stringify(import.meta.resolve("lucide-react")),
  );
const { default: Details } = await import(encode(compile(source, true)));
const report = {
  schema_version: 2,
  instrument: { name: "测试公司", instrument_id: "000001.SZ" },
  data_cutoff: "2026-10-22",
  valuation: { pb_mrq: 1.23, pb: 999 },
  company_profile: { state: "unavailable", reason: "本次没有主营构成" },
  industry_chain: { state: "unknown", items: [] },
  overview: { summary: "有依据的测试摘要" },
  claims: [{ kind: "title_clue", text: "测试公告线索", evidence_ids: ["e1"] }],
  news: {
    items: [
      {
        id: "n1",
        title: "测试计划公告",
        event_type: "业务线索",
        impact_label: "方向待核验",
        status: "计划线索",
        evidence_ids: ["e1"],
        url: "javascript:bad()",
      },
    ],
  },
  evidence: [
    {
      evidence_id: "e1",
      title: "已核验材料",
      source: "official",
      tier: "A",
      official_document: true,
      url: "https://example.com/report",
      data: { value: 1 },
    },
  ],
  source_coverage: [
    {
      source_id: "institution",
      title: "机构预测",
      state: "requires_license",
      reason: "本次未取得授权研报",
    },
  ],
  unresolved_questions: [
    { question: "是否已落地？", reason: "需原文核对", evidence_ids: ["e1"] },
  ],
  technical_assessment: {
    periods: [
      {
        period: "M",
        label: "月线",
        state: "unknown",
        observations: { close: null },
        rules: [{ label: "收盘>MA5", met: null }],
        levels: { support: null, resistance: null },
        current_period: { date: "2026-10-22", complete: false, close: 12 },
      },
    ],
    frozen_charts_by_period: { D: { bars: [] } },
  },
};
hooks.reset();
const html = renderToStaticMarkup(
  createElement(Details, {
    report,
    onMaterial: () => {
      throw Error("render must not open material");
    },
    legacyDetails: createElement("span", null, "LEGACY_DETAILS"),
  }),
);
for (const name of [
  "overview",
  "company",
  "valuation",
  "news",
  "technical",
  "evidence",
])
  assert.ok(html.includes('id="research-v2-' + name + '"'));
for (const text of [
  "标题线索",
  "方向待核验",
  "本次未取得授权研报",
  "完整财务、估值、股东及原始材料明细",
  "LEGACY_DETAILS",
  "形成中 / 不完整周期",
  "证据 1",
  "https://example.com/report",
])
  assert.ok(html.includes(text), text);
assert.ok(html.includes('href="#research-evidence-0"'));
assert.ok(html.includes("1.23"), "PB uses the provider's pb_mrq field");
assert.ok(
  !html.includes("999"),
  "uncontracted pb shadow cannot replace PB/MRQ",
);
assert.ok(!html.includes('href="javascript:'));
assert.ok(!html.includes("上涨概率100"));
// Exercise the real financial renderer: a profitable amount can still have a
// declining comparison, and a narrowing loss must not use a negative denominator.
const { ResearchFinancialValue: FinancialValue } = await import(financialValue);
const fiscal = (year, values) => ({
  thscode: "000001.SZ",
  source: "fuyao",
  currency: "CNY",
  unit: "CNY；basic_eps为元/股",
  period: "quarterly",
  fiscal_year: year,
  fiscal_period: "Q2",
  report_period: `${year}-06-30`,
  period_end_ms: Date.parse(`${year}-06-30T00:00:00+08:00`),
  report_date_ms: Date.parse(`${year}-08-20T00:00:00+08:00`),
  published_at_ms: Date.parse(`${year}-08-20T00:00:00+08:00`),
  ...values,
});
const financialRows = [
  fiscal(2026, {
    parent_holder_net_profit: 50,
    operating_income: 100,
    invest_cash_flow_net: -30,
  }),
  fiscal(2025, {
    parent_holder_net_profit: 100,
    operating_income: 80,
    invest_cash_flow_net: -60,
  }),
];
const financialHtml = (rows, field) =>
  renderToStaticMarkup(
    createElement(FinancialValue, { rows, row: rows[0], field }),
  );
const profitHtml = financialHtml(financialRows, "parent_holder_net_profit");
assert.match(profitHtml, /class="research-change increase">50\.00元/);
assert.match(
  profitHtml,
  /research-financial-comparison research-change decrease/,
);
assert.ok(profitHtml.includes("同比 -50.00%"));
assert.ok(profitHtml.includes("较上年同期 2025-06-30"));
const revenueHtml = financialHtml(financialRows, "operating_income");
assert.match(
  revenueHtml,
  /<span>100\.00元<\/span>/,
  "positive revenue is not itself growth",
);
assert.ok(revenueHtml.includes("同比 +25.00%"));
const flowHtml = financialHtml(financialRows, "invest_cash_flow_net");
assert.match(flowHtml, /class="research-change decrease">-30\.00元/);
assert.match(
  flowHtml,
  /research-financial-comparison research-change increase/,
);
assert.ok(
  !flowHtml.includes("亏损") &&
    !flowHtml.includes("恶化") &&
    !flowHtml.includes("%"),
);
const lossHtml = financialHtml(
  [fiscal(2026, { net_profit: -50 }), fiscal(2025, { net_profit: -100 })],
  "net_profit",
);
assert.ok(lossHtml.includes("亏损收窄") && !lossHtml.includes("%"));
for (const value of [null, "12", NaN, Infinity]) {
  const unknownHtml = financialHtml(
    [fiscal(2026, { net_profit: value })],
    "net_profit",
  );
  assert.match(unknownHtml, /class="research-change flat">—/);
  assert.ok(unknownHtml.includes("同比待核验"));
}
const zeroHtml = financialHtml(
  [fiscal(2026, { net_profit: 0 }), fiscal(2025, { net_profit: 0 })],
  "net_profit",
);
assert.match(zeroHtml, /class="research-change flat">0\.00元/);
assert.ok(zeroHtml.includes("同比持平"));
assert.equal(
  globalThis.__researchFrozenProps,
  undefined,
  "frozen chart waits until the trend chapter opens",
);
assert.match(html, /role="tab"[^>]*aria-selected="true"/);
assert.match(html, /id="research-v2-evidence"[^>]*hidden=""/);
assert.match(html, /id="research-v2-technical"[^>]*hidden=""/);
assert.ok(
  !html.includes("details open"),
  "source, question and metadata drawers start closed",
);
hooks.reset();
const missing = renderToStaticMarkup(
  createElement(Details, {
    report: { schema_version: 2, instrument: {} },
    onMaterial: () => {},
    legacyDetails: null,
  }),
);
assert.ok(missing.includes("本次没有取得可引用材料"));
assert.equal(globalThis.__researchFrozenProps, undefined);

// Move between the actual chapter controls and follow a citation across pages.
const largeReport = {
  ...report,
  claims: [{ kind: "fact", text: "第十八条的事实", evidence_ids: ["e18"] }],
  evidence: Array.from({ length: 21 }, (_, index) => ({
    evidence_id: "e" + (index + 1),
    title: "材料" + (index + 1),
    kind: index % 2 ? "announcement" : "financial_income",
    source: "真实来源",
    report_period: index < 10 ? "2025-4" : "2026-2",
    published_at_ms: index % 3 ? 1700000000000 : null,
  })),
};
function collectElements(element, predicate, found = []) {
  if (Array.isArray(element)) {
    element.forEach((child) => collectElements(child, predicate, found));
    return found;
  }
  if (!element || typeof element !== "object") return found;
  if (typeof element.type === "function") {
    collectElements(element.type(element.props), predicate, found);
    return found;
  }
  if (predicate(element)) found.push(element);
  collectElements(element.props?.children, predicate, found);
  return found;
}
function renderReader(value = largeReport) {
  hooks.begin();
  return Details({ report: value, onMaterial: () => {}, legacyDetails: null });
}
hooks.reset();
let tree = renderReader();
const tabs = () => collectElements(tree, (node) => node.props?.role === "tab");
assert.equal(tabs().length, 6);
assert.equal(
  tabs().find((tab) => tab.props["aria-selected"]).props.id,
  "research-tab-overview",
);
const oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
const oldLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
const scrolled = [];
try {
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      getElementById: (id) => ({
        scrollIntoView: (options) => scrolled.push({ id, options }),
        focus: () => {},
      }),
    },
  });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { hash: "#research" },
  });
  tabs()
    .find((tab) => tab.props.id === "research-tab-technical")
    .props.onClick();
  tree = renderReader();
  renderToStaticMarkup(tree);
  assert.deepEqual(
    globalThis.__researchFrozenProps.frozenChartsByPeriod,
    report.technical_assessment.frozen_charts_by_period,
  );
  assert.equal(globalThis.__researchFrozenProps.showStockSummary, false);
  const citation = collectElements(
    tree,
    (node) => node.type === "a" && node.props.href === "#research-evidence-17",
  )[0];
  let prevented = false;
  citation.props.onClick({
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  tree = renderReader();
  hooks.flush();
  const evidencePanel = collectElements(
    tree,
    (node) => node.props?.id === "research-v2-evidence",
  )[0];
  assert.equal(evidencePanel.props.hidden, false);
  const articleIds = collectElements(
    tree,
    (node) =>
      node.type === "article" &&
      node.props.id?.startsWith("research-evidence-"),
  ).map((node) => node.props.id);
  assert.deepEqual(articleIds, [
    "research-evidence-16",
    "research-evidence-17",
    "research-evidence-18",
    "research-evidence-19",
    "research-evidence-20",
  ]);
  assert.equal(scrolled.at(-1).id, "research-evidence-17");
  assert.equal(
    globalThis.location.hash,
    "#research",
    "citations and chapter switching preserve the application route",
  );
  const filters = collectElements(
    tree,
    (node) => node.type === "input" && node.props.type === "search",
  );
  filters[0].props.onChange({ target: { value: "材料21" } });
  tree = renderReader();
  assert.equal(
    collectElements(
      tree,
      (node) =>
        node.type === "article" &&
        node.props.id?.startsWith("research-evidence-"),
    ).length,
    1,
  );
  const originalLink = collectElements(
    tree,
    (node) => node.type === "a" && node.props.href === "#research-evidence-17",
  )[0];
  originalLink.props.onClick({ preventDefault: () => {} });
  tree = renderReader();
  hooks.flush();
  assert.equal(
    collectElements(
      tree,
      (node) => node.type === "input" && node.props.type === "search",
    )[0].props.value,
    "",
  );
  assert.equal(
    scrolled.at(-1).id,
    "research-evidence-17",
    "citation clears incompatible filters before paging",
  );
  tabs()
    .find((tab) => tab.props.id === "research-tab-evidence")
    .props.onKeyDown({ key: "Home", preventDefault: () => {} });
  tree = renderReader();
  assert.equal(
    tabs().find((tab) => tab.props["aria-selected"]).props.id,
    "research-tab-overview",
  );
} finally {
  if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument);
  else delete globalThis.document;
  if (oldLocation) Object.defineProperty(globalThis, "location", oldLocation);
  else delete globalThis.location;
}
const frozen = JSON.stringify(largeReport.evidence);
assert.equal(
  researchEvidencePage(largeReport.evidence, {
    query: "2026-2",
    kind: "financial",
    dateState: "undated",
  }).total,
  2,
);
assert.equal(researchEvidencePage(largeReport.evidence, { page: 99 }).page, 2);
assert.equal(researchEvidenceKind("financial_cashflow"), "financial");
assert.equal(
  researchEvidencePage(largeReport.evidence, { query: "不存在" }).rows.length,
  0,
);
assert.equal(
  JSON.stringify(largeReport.evidence),
  frozen,
  "filtering never modifies retained records",
);
const duplicates = [
  { question: "同一问题", reason: "相同原因", evidence_ids: ["a"] },
  { question: "同一问题", reason: "相同原因", evidence_ids: ["b"] },
  { question: "同一问题", reason: "另一原因", evidence_ids: [] },
];
const grouped = groupResearchQuestions(duplicates);
assert.equal(grouped.length, 2);
assert.deepEqual(grouped[0].evidence_ids, ["a", "b"]);
assert.equal(grouped[0].count, 2);
assert.equal(
  duplicates.length,
  3,
  "aggregation preserves every original question",
);

// The actual top-level compatibility switch retains the old component for v1.
const detailsSource = await fs.readFile(
  new URL("./ResearchDetails.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "ResearchDetails.tsx",
  detailsSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const wrapper = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "ResearchDetails",
);
const wrapperSource =
  `import React from ${react};const ResearchEvidenceDetails=()=>React.createElement('div',null,'V2');const LegacyResearchDetails=()=>React.createElement('div',null,'V1');` +
  wrapper.getText(ast);
const { default: Wrapper } = await import(encode(compile(wrapperSource, true)));
assert.ok(
  renderToStaticMarkup(
    createElement(Wrapper, { report: {}, onMaterial: () => {} }),
  ).includes("V1"),
);
assert.ok(
  renderToStaticMarkup(
    createElement(Wrapper, {
      report: { schema_version: 2 },
      onMaterial: () => {},
    }),
  ).includes("V2"),
);
console.log(
  "research v2 six sections, traceable evidence, unknown/source semantics, frozen chart handoff and v1 compatibility passed",
);
