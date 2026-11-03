import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const transpile = (text, fileName = "part.tsx") =>
  ts.transpileModule(text, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const module = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const { stockHeaderQuote } = await import(
  module(
    transpile(
      await fs.readFile(
        new URL("./stockQuotePresentation.ts", import.meta.url),
        "utf8",
      ),
    ),
  )
);
const id = "601900.SH",
  asOf = "2026-10-23";
const detail = {
  instrument: { instrument_id: id, name: "南方传媒", kind: "stock" },
  data_date: asOf,
  date: asOf,
  close: 12.87,
  change_pct: -1.6055,
  price_basis: "none",
  price_continuity: true,
  corporate_action: false,
};
assert.equal(stockHeaderQuote(detail, id, asOf).price, 12.87);
assert.equal(stockHeaderQuote(detail, id, asOf).change, -1.6055);
assert.match(stockHeaderQuote(detail, id, asOf).label, /2026-10-23 收盘/);
const previous = stockHeaderQuote({ ...detail, date: "2026-10-22" }, id, asOf);
assert.equal(previous.price, detail.close);
assert.equal(previous.stale, true);
assert.match(previous.label, /截至 2026-10-23 最近收盘.*报价 2026-10-22/);
for (const invalid of [
  null,
  { ...detail, close: null },
  { ...detail, close: 0 },
  { ...detail, close: "12.87" },
  { ...detail, close: Infinity },
  { ...detail, date: "2026-10-24" },
  { ...detail, date: "2026-02-30" },
  { ...detail, data_date: "2026-10-22" },
  { ...detail, price_basis: "forward_event_affine" },
  { ...detail, instrument: { instrument_id: "600519.SH" } },
]) {
  const result = stockHeaderQuote(invalid, id, asOf);
  assert.equal(result.price, null);
  assert.equal(result.change, null);
}
for (const invalidChange of [
  { change_pct: null },
  { change_pct: 0, corporate_action: true },
  { change_pct: 5, price_continuity: false },
]) {
  const result = stockHeaderQuote({ ...detail, ...invalidChange }, id, asOf);
  assert.equal(result.price, detail.close);
  assert.equal(result.change, null);
}
assert.equal(
  stockHeaderQuote({ ...detail, change_pct: 0 }, id, asOf).change,
  0,
);

function declaration(source, name) {
  const ast = ts.createSourceFile(
    "part.tsx",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const node = ast.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
  return transpile(node.getText(ast).replace(/^export (default )?/, ""))
    .replace(/^import .+;\n/gm, "")
    .replace(/export \{\};?\n?/g, "");
}
const quoteComponent = declaration(
  await fs.readFile(new URL("./StockHeaderQuote.tsx", import.meta.url), "utf8"),
  "StockHeaderQuote",
);
const num = (value) => (value == null ? "—" : Number(value).toFixed(2));
const percent = (value) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${num(value)}%`;
const tone = (value) => (value > 0 ? "up" : value < 0 ? "down" : "muted");
const StockHeaderQuote = new Function(
  "_jsx",
  "_jsxs",
  "stockHeaderQuote",
  "num",
  "percent",
  "tone",
  quoteComponent + ";return StockHeaderQuote;",
)(jsx, jsxs, stockHeaderQuote, num, percent, tone);
const chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const drawerCode = declaration(chartSource, "StockDrawer");
const readTs = async (path) =>
  transpile(await fs.readFile(new URL(path, import.meta.url), "utf8"));
const jsxImports = (text) =>
  text.replaceAll(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  );
const { stockPatternHeader } = await import(
  module(await readTs("./stockPatternHeader.ts"))
);
const { PatternStatus, PATTERN_BASIS } = await import(
  module(jsxImports(await readTs("./CandleLimitEvents.tsx")))
);
const ladderModule = module(
  await readTs("../features/market/ladderPresentation.ts"),
);
const boardModule = module(
  (await readTs("../features/market/boardPresentation.ts")).replaceAll(
    'from "./ladderPresentation"',
    `from ${JSON.stringify(ladderModule)}`,
  ),
);
const { default: BoardTags } = await import(
  module(
    jsxImports(await readTs("../features/market/BoardTags.tsx"))
      .replaceAll(
        'from "./boardPresentation"',
        `from ${JSON.stringify(boardModule)}`,
      )
      .replace(/^import ["'][^"']+\.css["'];?$/gm, ""),
  )
);
const { stockExtraMetrics } = await import(
  module(await readTs("./stockExtraMetrics.ts"))
);
const compactComponent = declaration(
  await fs.readFile(
    new URL("./StockCompactMetrics.tsx", import.meta.url),
    "utf8",
  ),
  "StockCompactMetrics",
);
const StockCompactMetrics = new Function(
  "_jsx",
  "_jsxs",
  "stockExtraMetrics",
  "stockHeaderQuote",
  "num",
  "percent",
  "tone",
  compactComponent + ";return StockCompactMetrics;",
)(jsx, jsxs, stockExtraMetrics, stockHeaderQuote, num, percent, tone);
const { stockNavigationPosition } = await import(
  module(await readTs("./stockNavigation.ts"))
);
let loaded = null,
  headerBoard = null,
  periodSelection = null,
  stateIndex = 0;
const requests = [];
const scopeValues = {
  React,
  _jsx: jsx,
  _jsxs: jsxs,
  ...React,
  // Provide saved chart and header-board state, then use defaults for controls.
  useState: (initial) => [
    [loaded, headerBoard, periodSelection][stateIndex++] ?? initial,
    () => {},
  ],
  useRef: (current) => ({ current }),
  useEffect: () => {},
  useApi: (path) => {
    requests.push(path);
    return { data: detail };
  },
  stockPayloadMatches: (payload, symbol, observed) =>
    payload?.instrument?.instrument_id === symbol &&
    (payload.as_of || payload.data_date) === observed,
  stockNavigationPosition,
  useStockEventBaseline: () => ({}),
  StockHeaderQuote,
  CandleChart: ({ controlledPeriod }) =>
    jsx("div", { "data-test-chart-period": controlledPeriod }),
  StockCompactMetrics,
  CopyStockCode: ({ code }) =>
    jsx("button", { "data-copy-wired": true, children: code }),
  stockPatternHeader,
  PatternStatus,
  BoardTags,
  num,
  percent,
};
const scope = new Proxy(scopeValues, {
  has: () => true,
  get: (target, key) => {
    if (key === Symbol.unscopables) return undefined;
    if (key in target) return target[key];
    if (key in globalThis) return globalThis[key];
    return () => null;
  },
});
const Drawer = new Function(
  "scope",
  `with(scope){${drawerCode};return StockDrawer;}`,
)(scope);
for (const period of ["D", "W", "M"]) {
  for (const price_basis of ["none", "forward_event_affine"]) {
    loaded = {
      id,
      asOf,
      data: {
        instrument: detail.instrument,
        as_of: asOf,
        period,
        price_basis,
        bars: [{ date: "2020-01-01", close: 9999, change_pct: 888 }],
      },
    };
    stateIndex = 0;
    const html = renderToStaticMarkup(
      jsx(Drawer, { id, asOf, onClose: () => {} }),
    );
    assert.match(html, /stock-title-row/);
    assert.match(html, /12\.87/);
    assert.match(html, /原始收盘报价/);
    assert.match(html, /-1\.61%/);
    assert.match(html, /2026-10-23 收盘.*未复权.*非实时/);
    assert.ok(
      !html.includes("9999") && !html.includes("888%"),
      "hover/aggregate chart prices never replace header quote",
    );
  }
}

// The fixed identity row receives dated height/source text and retains the
// independently verified daily shape, without repeating it through BoardTags.
const snapshotId = "synthetic-header-generation";
Object.assign(detail, {
  snapshot_id: snapshotId,
  pattern_as_of: asOf,
  pattern_date: asOf,
  pattern_events: [
    {
      date: asOf,
      bar_date: asOf,
      snapshot_id: snapshotId,
      kind: "earth_to_sky",
      basis: PATTERN_BASIS,
      raw_price_basis: "none",
      raw_volume: 100,
      raw_ohlc: { open: 9, high: 11, low: 9, close: 11 },
      source: "fuyao",
    },
  ],
});
const boardRow = {
  instrument_id: id,
  date: asOf,
  snapshot_id: snapshotId,
  board_presentation: {
    version: "dated-stock-board-presentation-v1",
    instrument_id: id,
    date: asOf,
    snapshot_id: snapshotId,
    height: { value: 1, label: "首板", state: "confirmed", source_date: asOf },
    board_text: {
      label: "5天3板",
      state: "reported",
      source: "fuyao",
      source_date: asOf,
    },
    types: [
      {
        code: "earth_to_sky",
        label: "地天板",
        state: "confirmed",
        source_date: asOf,
      },
    ],
  },
};
const renderHeader = (value) => {
  headerBoard = value;
  stateIndex = 0;
  const html = renderToStaticMarkup(
    jsx(Drawer, { id, asOf, onClose: () => {} }),
  );
  return html.slice(
    html.indexOf('class="entry-page-bar"'),
    html.indexOf('class="entry-page-scroll"'),
  );
};
const currentHeader = renderHeader({ row: boardRow, date: asOf });
assert.match(currentHeader, /首板/);
assert.match(currentHeader, /5天3板/);
assert.match(currentHeader, /data-copy-wired="true">601900/);
assert.match(currentHeader, /观察日行情摘要/);
assert.match(currentHeader, /aria-label="K线周期"/);
assert.match(currentHeader, /aria-pressed="true">日 K/);
assert.match(currentHeader, /aria-pressed="false">周 K/);
assert.match(currentHeader, /aria-pressed="false">月 K/);
periodSelection = { id, value: "W" };
assert.match(renderHeader({ row: boardRow, date: asOf }), /aria-pressed="true">周 K/);
stateIndex = 0;
assert.match(renderToStaticMarkup(jsx(Drawer, { id, asOf, onClose: () => {} })),
  /data-test-chart-period="W"/);
periodSelection = null;
assert.match(currentHeader, /今日涨幅/);
assert.match(currentHeader, /近30交易日/);
assert.match(currentHeader, /年内涨停/);
const navigation = {
  source: "members",
  scopeKey: "sample",
  label: "样本成员",
  asOf,
  items: [{ id: "600001.SH" }, { id }, { id: "600002.SH" }],
};
stateIndex = 0;
const navigable = renderToStaticMarkup(
  jsx(Drawer, { id, asOf, navigation, onStep: () => {}, onClose: () => {} }),
);
const fixedNav = navigable.slice(
  navigable.indexOf('class="entry-page-bar"'),
  navigable.indexOf('class="entry-page-scroll"'),
);
assert.match(fixedNav, /上一只股票/);
assert.match(fixedNav, /下一只股票/);
assert.match(fixedNav, /2 \/ 3/);
assert.equal(
  (navigable.match(/aria-label="上一只股票"/g) || []).length,
  1,
  "navigation stays in fixed bar without duplicate controls above",
);
let steps = [];
stateIndex = 0;
const tree = Drawer({
  id,
  asOf,
  navigation,
  onStep: (direction) => steps.push(direction),
  onClose: () => {},
});
const find = (node, label) => {
  if (!node || typeof node !== "object") return null;
  if (node.props?.["aria-label"] === label) return node;
  const children = Array.isArray(node.props?.children)
    ? node.props.children
    : [node.props?.children];
  for (const child of children) {
    const found = find(child, label);
    if (found) return found;
  }
  return null;
};
find(tree, "上一只股票").props.onClick();
find(tree, "下一只股票").props.onClick();
assert.deepEqual(steps, [-1, 1]);
assert.equal(
  (currentHeader.match(/>地天板</g) || []).length,
  1,
  "the original verified shape stays in the identity row and is not repeated by BoardTags",
);
assert.equal((currentHeader.match(/data-board-tag="height"/g) || []).length, 1);
for (const invalid of [
  null,
  { row: { ...boardRow, instrument_id: "600519.SH" }, date: asOf },
  { row: boardRow, date: "2026-10-22" },
  { row: { ...boardRow, snapshot_id: "stale-generation" }, date: asOf },
]) {
  const header = renderHeader(invalid);
  assert.ok(
    !header.includes("首板") && !header.includes("5天3板"),
    "switching instrument/date/generation never retains a stale summary height",
  );
  assert.match(
    header,
    />地天板</,
    "the daily-detail shape remains independently sourced",
  );
}

assert.deepEqual(
  [...new Set(requests.filter(Boolean))],
  [`/instruments/${id}?as_of=${asOf}`],
  "header uses existing daily detail request only",
);
console.log(
  "stock header: fixed real daily detail, dated older quote, unknown guards, D/W/M and raw/forward chart independence passed",
);
