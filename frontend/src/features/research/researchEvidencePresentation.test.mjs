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
const { researchUrl, researchEvidenceRefs, researchClaim, researchRows } =
  await import(helper);
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
const react = JSON.stringify(import.meta.resolve("react"));
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
  .replace('"../../lib/format"', JSON.stringify(format))
  .replace('"../../components/UI"', JSON.stringify(ui))
  .replace('"../../components/Charts"', JSON.stringify(charts))
  .replace('"../../components/CopyStockCode"', JSON.stringify(copy))
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
assert.deepEqual(
  globalThis.__researchFrozenProps.frozenChartsByPeriod,
  report.technical_assessment.frozen_charts_by_period,
);
assert.equal(globalThis.__researchFrozenProps.showStockSummary, false);
const missing = renderToStaticMarkup(
  createElement(Details, {
    report: { schema_version: 2, instrument: {} },
    onMaterial: () => {},
    legacyDetails: null,
  }),
);
assert.ok(missing.includes("本次没有取得可引用材料"));
assert.deepEqual(
  globalThis.__researchFrozenProps.frozenChartsByPeriod,
  {},
  "missing frozen charts cannot request current market data",
);

// Click the actual component's internal anchors, not just an isolated helper.
const internalLinks = [];
function collectInternalLinks(element) {
  if (Array.isArray(element)) {
    element.forEach(collectInternalLinks);
    return;
  }
  if (!element || typeof element !== "object") return;
  if (typeof element.type === "function") {
    collectInternalLinks(element.type(element.props));
    return;
  }
  if (element.type === "a" && element.props.href?.startsWith("#"))
    internalLinks.push(element.props);
  collectInternalLinks(element.props?.children);
}
collectInternalLinks(
  Details({ report, onMaterial: () => {}, legacyDetails: null }),
);
assert.ok(
  internalLinks.length > 6,
  "six section links and real evidence references are exercised",
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
      }),
    },
  });
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { hash: "#research" },
  });
  for (const link of internalLinks) {
    let prevented = false;
    assert.equal(typeof link.onClick, "function");
    link.onClick({
      preventDefault: () => {
        prevented = true;
      },
    });
    assert.equal(
      prevented,
      true,
      "internal anchor must prevent default hash navigation",
    );
    assert.equal(scrolled.at(-1).id, link.href.slice(1));
    assert.deepEqual(scrolled.at(-1).options, {
      behavior: "smooth",
      block: "start",
    });
    assert.equal(
      globalThis.location.hash,
      "#research",
      "research route must stay selected",
    );
  }
} finally {
  if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument);
  else delete globalThis.document;
  if (oldLocation) Object.defineProperty(globalThis, "location", oldLocation);
  else delete globalThis.location;
}

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
