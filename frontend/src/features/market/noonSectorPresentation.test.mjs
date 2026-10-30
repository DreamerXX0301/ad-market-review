import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./noonSectorPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { noonSectorEndpoint } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const snapshot = "noon_" + "1".repeat(32);
assert.equal(
  noonSectorEndpoint(snapshot, "884001.TI", { board: "star", page: 2 }),
  `/market/noon/${snapshot}/sector-members?topic_code=884001.TI&board=star&page=2`,
);
assert.equal(noonSectorEndpoint("../current", "884001.TI"), null);
assert.equal(noonSectorEndpoint(snapshot, "same-name"), null);

const navSource = await fs.readFile(
  new URL("./noonPresentation.ts", import.meta.url),
  "utf8",
);
const navCode = ts.transpileModule(navSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { noonSectorSelection } = await import(
  "data:text/javascript;base64," + Buffer.from(navCode).toString("base64")
);
const selection = noonSectorSelection(
  {
    instrument_id: "884001.TI",
    kind: "concept",
    noon_members_snapshot_id: snapshot,
  },
  "2026-10-28",
  [],
);
assert.equal(
  selection.topic.noon_members_snapshot_id,
  snapshot,
  "members stay readable even without any formal history",
);
assert.equal(
  selection.topic.noon_chart_as_of,
  undefined,
  "missing completed-day background cannot silently become today's close",
);
const root = await fs.readFile(
  new URL("../../App.tsx", import.meta.url),
  "utf8",
);
assert.ok(root.includes("sector.topic.noon_members_snapshot_id ?"));
assert.ok(root.includes("<NoonSectorDialog"));
const review = await fs.readFile(
  new URL("./NoonReview.tsx", import.meta.url),
  "utf8",
);
assert.match(
  review,
  /noon_members_snapshot_id:\s*snapshot\.snapshot_id/,
  "actual noon click passes its capture identity",
);

const { build } = await import("vite");
const { createRequire } = await import("node:module");
const { pathToFileURL } = await import("node:url");
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const require = createRequire(import.meta.url);
const bundle = await build({
  configFile: false,
  root: new URL("../../../", import.meta.url).pathname,
  logLevel: "silent",
  build: {
    write: false,
    ssr: new URL("./NoonSectorDialog.tsx", import.meta.url).pathname,
    minify: false,
  },
});
const entry = bundle.output.find(
  (item) => item.type === "chunk" && item.isEntry,
);
const code = entry.code.replace(
  /from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`,
);
const { NoonSectorQuote } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const priorFetch = globalThis.fetch;
const dialogStyles = await fs.readFile(
  new URL("./NoonSectorDialog.css", import.meta.url),
  "utf8",
);
assert.match(
  dialogStyles,
  /\.noon-sector-values \.up\s*\{\s*color:\s*var\(--red\)/,
  "compact chip gains retain the existing market red token",
);
assert.match(
  dialogStyles,
  /\.noon-sector-values \.down\s*\{\s*color:\s*var\(--green\)/,
  "compact chip losses retain the existing market green token",
);
globalThis.fetch = () => {
  throw new Error("frozen rendering must not fetch");
};
try {
  const html = renderToStaticMarkup(
    createElement(NoonSectorQuote, {
      data: {
        state: "available",
        date: "2026-10-28",
        collected_start_at: "2026-10-28T11:32:00+08:00",
        collected_end_at: "2026-10-28T11:34:00+08:00",
        sector_quote: {
          latest_price: 11,
          open: 10,
          high: 12,
          low: 9,
          change_pct: 10,
        },
      },
    }),
  );
  assert.ok(
    html.includes("午间形成中") &&
      html.includes("+10.00%") &&
      html.includes("逐条报价时刻"),
  );
  assert.ok(
    html.includes("不属于正式日K") && html.includes("不纳入正式日K或选股指标"),
  );
  assert.ok(
    !html.includes("<svg"),
    "complete OHLC is shown as compact quote chips, not a large isolated candle",
  );
  assert.equal(
    (html.match(/class="member-context-item"/g) || []).length,
    6,
    "quote summary reuses the industry member metadata chips",
  );
  assert.ok(
    html.includes("member-dialog-context") &&
      html.includes("午间涨幅") &&
      html.includes("午间成交额"),
  );
  assert.ok(
    html.includes("未提供"),
    "batch collection range cannot be claimed as an individual quote timestamp",
  );
  const missing = renderToStaticMarkup(
    createElement(NoonSectorQuote, { data: { sector_quote: {} } }),
  );
  assert.ok(
    !missing.includes("<svg"),
    "missing quote never manufactures a candle",
  );
  assert.ok(
    missing.includes("日期未核验") &&
      missing.includes("—") &&
      !missing.includes("0.00"),
    "unknown quote and date remain explicit",
  );
} finally {
  globalThis.fetch = priorFetch;
}
console.log(
  "noon sector: immutable identity, compact frozen quote chips, no large candle or history backfill passed",
);

// Exercise the actual dialog's button handlers and generated API query. Fixtures
// deliberately separate board identity from quote availability.
const encoded = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const state = [],
  effects = [],
  scheduled = [];
let cursor = 0,
  tree,
  timers = [],
  paths = [],
  openedStocks = [],
  resultOverride = null;
const equal = (a, b) =>
  a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
globalThis.__noonSectorHooks = {
  useState(initial) {
    const index = cursor++;
    if (!(index in state)) state[index] = initial;
    return [
      state[index],
      (next) => {
        state[index] = typeof next === "function" ? next(state[index]) : next;
      },
    ];
  },
  useEffect(callback, dependencies) {
    const index = cursor++;
    if (!effects[index] || !equal(effects[index].dependencies, dependencies))
      scheduled.push(() => {
        effects[index]?.cleanup?.();
        effects[index] = { dependencies, cleanup: callback() };
      });
  },
};
const fixtureMembers = [
  {
    instrument_id: "600001.SH",
    name: "合成缺报价主板",
    board: "main",
    quote_available: false,
    change_pct: null,
    latest_price: null,
  },
  ...Array.from({ length: 50 }, (_, index) => ({
    instrument_id: `60${String(index + 2).padStart(4, "0")}.SH`,
    name: `合成主板${index}`,
    board: "main",
    quote_available: true,
    change_pct: 1,
  })),
  {
    instrument_id: "300001.SZ",
    name: "合成创业",
    board: "chinext",
    quote_available: true,
    change_pct: 2,
  },
  {
    instrument_id: "688001.SH",
    name: "合成科创",
    board: "star",
    quote_available: true,
    change_pct: 3,
  },
  {
    instrument_id: "920001.BJ",
    name: "合成北交",
    board: "beijing",
    quote_available: true,
    change_pct: 4,
  },
  {
    instrument_id: "fixture-unknown",
    name: "合成板别未核验",
    board: "unknown",
    quote_available: true,
    change_pct: 5,
  },
];
globalThis.__noonSectorAPI = {
  useApi(path) {
    paths.push(path);
    const params = new URL(path, "http://fixture.invalid").searchParams;
    const board = params.get("board"),
      page = Number(params.get("page"));
    const filtered = fixtureMembers.filter(
      (row) => board === "all" || row.board === board,
    );
    return {
      loading: false,
      error: "",
      reload() {},
      data: {
        state: "available",
        date: "2026-10-29",
        membership_as_of: "2026-10-21",
        formal_chart_as_of: "2026-10-28",
        sector_quote: {},
        total: fixtureMembers.length,
        matched: filtered.length,
        page,
        pages: Math.max(1, Math.ceil(filtered.length / 50)),
        items: filtered.slice((page - 1) * 50, page * 50),
        ...resultOverride,
      },
    };
  },
};
let controllerSource = ts.transpileModule(
  await fs.readFile(new URL("./NoonSectorDialog.tsx", import.meta.url), "utf8"),
  {
    fileName: "NoonSectorDialog.tsx",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
).outputText;
const boardCode = ts.transpileModule(
  await fs.readFile(
    new URL("./noonBoardPresentation.ts", import.meta.url),
    "utf8",
  ),
  { compilerOptions: { module: ts.ModuleKind.ES2022 } },
).outputText;
for (const [from, to] of Object.entries({
  react: encoded(
    "export const {useState,useEffect}=globalThis.__noonSectorHooks;",
  ),
  "react/jsx-runtime": encoded(
    "export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const Fragment='fragment';",
  ),
  "../../lib/api": encoded("export const {useApi}=globalThis.__noonSectorAPI;"),
  "../../lib/format": encoded(
    "export const amount=v=>v??'—',num=v=>v??'—',percent=v=>v??'—',tone=v=>v==null?'':v<0?'down':'up';",
  ),
  "../../components/UI": encoded(
    "export const Badge='Badge',Empty='Empty',ErrorBox='ErrorBox',Loading='Loading',Modal='Modal',Table='Table',Warning='Warning';",
  ),
  "../../components/Charts": encoded("export const CandleChart='CandleChart';"),
  "./noonBoardPresentation": encoded(boardCode),
  "./noonPresentation": encoded("export const noonClock=v=>v||'—';"),
  "./noonSectorPresentation": encoded(compiled),
}))
  controllerSource = controllerSource.replaceAll(
    JSON.stringify(from),
    JSON.stringify(to),
  );
controllerSource = controllerSource.replace(/import "[^\"]+\.css";/g, "");
const { default: Dialog } = await import(encoded(controllerSource));
const originalSetTimeout = globalThis.setTimeout,
  originalClearTimeout = globalThis.clearTimeout;
globalThis.setTimeout = (callback) => {
  const item = { callback, cancelled: false };
  timers.push(item);
  return item;
};
globalThis.clearTimeout = (item) => {
  if (item) item.cancelled = true;
};
let embedded = false;
const render = () => {
  cursor = 0;
  tree = Dialog({
    topic: { topic_code: "884001.TI", name: "合成概念" },
    snapshotId: snapshot,
    embedded,
    onClose() {},
    onStock: (...args) => openedStocks.push(args),
  });
  scheduled.splice(0).forEach((effect) => effect());
  return tree;
};
const nodes = (value) =>
  !value || typeof value !== "object"
    ? []
    : Array.isArray(value)
      ? value.flatMap(nodes)
      : [value, ...nodes(value.props?.children)];
const text = (value) =>
  value == null
    ? ""
    : typeof value === "object"
      ? Array.isArray(value)
        ? value.map(text).join("")
        : text(value.props?.children)
      : String(value);
const button = (label) =>
  nodes(tree).find((node) => node.type === "button" && text(node) === label);
const group = () =>
  nodes(tree).find(
    (node) =>
      node.props?.role === "group" &&
      node.props["aria-label"] === "午间成分股上市板块筛选",
  );
const table = () => nodes(tree).find((node) => node.type === "Table");
const params = () =>
  new URL(paths.at(-1), "http://fixture.invalid").searchParams;
try {
  render();
  timers
    .splice(0)
    .filter((item) => !item.cancelled)
    .forEach((item) => item.callback());
  render();
  assert.deepEqual(
    nodes(group())
      .filter((node) => node.type === "button")
      .map((node) => text(node)),
    ["全部", "主板", "创业板", "科创板", "北交所", "待核验"],
  );
  assert.equal(button("全部").props["aria-pressed"], true);
  assert.ok(button("全部").props.className.includes("active"));
  assert.equal(
    nodes(group()).some((node) => node.type === "select"),
    false,
  );
  assert.ok(
    text(tree).includes("午间报价日2026-10-29") &&
      text(tree).includes("成员名单留存日2026-10-21"),
    "quote day and saved membership day remain distinct",
  );
  button("下一页").props.onClick();
  render();
  assert.equal(params().get("page"), "2");
  for (const [label, value] of [
    ["主板", "main"],
    ["创业板", "chinext"],
    ["科创板", "star"],
    ["北交所", "beijing"],
    ["待核验", "unknown"],
    ["全部", "all"],
  ]) {
    button(label).props.onClick();
    render();
    assert.equal(params().get("board"), value);
    assert.equal(params().get("page"), "1", "each shortcut begins at page one");
    assert.equal(button(label).props["aria-pressed"], true);
    assert.equal(
      nodes(group()).filter(
        (node) => node.type === "button" && node.props["aria-pressed"],
      ).length,
      1,
    );
    assert.ok(
      paths.at(-1).startsWith(`/market/noon/${snapshot}/sector-members?`),
      "board switches retain the immutable capture identity",
    );
  }
  button("主板").props.onClick();
  render();
  const missingQuote = table().props.rows.find(
    (row) => row.instrument_id === "600001.SH",
  );
  assert.equal(missingQuote.quote_available, false);
  assert.equal(
    missingQuote.change_pct,
    null,
    "known main board with no quote is retained without manufactured zero",
  );
  assert.equal(
    table()
      .props.columns.find((column) => column.key === "board")
      .render(missingQuote.board).props.children,
    "主板",
  );
  assert.equal(
    table()
      .props.columns.find((column) => column.key === "change_pct")
      .render(null).props.children,
    "—",
  );
  table().props.onStock(missingQuote.instrument_id);
  assert.deepEqual(
    openedStocks,
    [["600001.SH", "2026-10-28"]],
    "opening a member's formal chart stays strictly before the noon day",
  );
  button("待核验").props.onClick();
  render();
  assert.equal(table().props.rows.length, 1);
  assert.equal(
    table().props.rows[0].quote_available,
    true,
    "valid quote cannot turn an unknown board into a known one",
  );
  assert.equal(table().props.rows[0].board, "unknown");
  button("主板").props.onClick();
  render();
  nodes(tree)
    .find(
      (node) =>
        node.type === "input" && node.props.placeholder === "名称 / 代码",
    )
    .props.onChange({ target: { value: "合成" } });
  render();
  timers
    .splice(0)
    .filter((item) => !item.cancelled)
    .forEach((item) => item.callback());
  render();
  nodes(tree)
    .find((node) => node.type === "select" && node.props.value === "change_pct")
    .props.onChange({ target: { value: "amount" } });
  render();
  button("↓ 降序").props.onClick();
  render();
  button("下一页").props.onClick();
  render();
  assert.equal(params().get("page"), "2");
  button("科创板").props.onClick();
  render();
  assert.equal(params().get("page"), "1");
  assert.equal(params().get("query"), "合成");
  assert.equal(params().get("sort"), "amount");
  assert.equal(
    params().get("direction"),
    "asc",
    "shortcut combines with existing search and server sort",
  );
  assert.ok(
    nodes(tree).some((node) =>
      node.props?.className?.includes("market-members-body"),
    ),
    "member view uses the established wide member-dialog viewport",
  );
  assert.ok(
    nodes(tree).some(
      (node) => node.props?.className === "member-dialog-toolbar",
    ),
  );
  button("板块K线").props.onClick();
  render();
  assert.ok(
    nodes(tree).some((node) =>
      node.props?.className?.includes(
        "sector-chart-dialog-body entry-page-scroll",
      ),
    ),
    "chart view uses the established sector chart scroll viewport",
  );
  const chart = nodes(tree).find((node) => node.type === "CandleChart");
  assert.equal(chart.props.asOf, "2026-10-28");
  assert.equal(chart.props.height, 680);
  assert.equal(chart.props.entryFocus, true);
  assert.ok(
    chart.props.dataEndpoint.endsWith("&date=2026-10-28"),
    "compact noon summary does not change the background close day",
  );
  assert.ok(!nodes(tree).some((node) => node.type === "svg"));
  button("成分股").props.onClick();
  render();
  assert.equal(
    button("科创板").props["aria-pressed"],
    true,
    "view switches preserve the user's board filter",
  );
  resultOverride = {
    state: "unknown",
    formal_chart_as_of: null,
    unavailable_reason: "本份成员名单尚未核验",
    total: null,
    quote_coverage: null,
    items: [],
    matched: 0,
  };
  render();
  assert.ok(
    text(tree).includes("已知成员未知 只") &&
      text(tree).includes("可比报价未知 只"),
    "missing membership never becomes an empty known universe",
  );
  assert.equal(
    nodes(tree).find((node) => node.type === "Warning").props.children,
    "本份成员名单尚未核验",
  );
  assert.equal(table().props.emptyTitle, "午间成员尚未核验");
  assert.ok(
    text(tree).includes("匹配数量未知") && !text(tree).includes("匹配 0 只"),
    "an unknown roster is not presented as a known empty match",
  );
  button("板块K线").props.onClick();
  render();
  assert.equal(
    nodes(tree).some((node) => node.type === "CandleChart"),
    false,
    "unverified previous day cannot open later history",
  );
  assert.equal(
    nodes(tree).find((node) => node.type === "Empty").props.title,
    "未核验前一交易日",
  );
  button("成分股").props.onClick();
  embedded = true;
  resultOverride = null;
  render();
  assert.equal(nodes(tree).some((node) => node.type === "Modal"), false, "inline members do not open a modal");
  assert.equal(nodes(tree).some((node) => node.props?.role === "tablist"), false, "inline members keep chart actions in the parent heading");
  assert.ok(nodes(tree).some((node) => node.type === "details" && text(node).includes("板块报价与采集时间") && !node.props.open));
  assert.ok(table(), "inline mode reuses the actual frozen member table");
  button("创业板").props.onClick();
  render();
  assert.equal(params().get("board"), "chinext");
  assert.ok(paths.at(-1).startsWith(`/market/noon/${snapshot}/sector-members?`), "inline filter stays bound to the immutable capture");
  console.log(
    "Noon sector shortcuts: six accessible buttons, immutable snapshot/board query, page reset, search/sort composition, shared member/chart layout, unknown board and missing quotes, prior-day Kline scope passed",
  );
} finally {
  effects.forEach((effect) => effect?.cleanup?.());
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  delete globalThis.__noonSectorHooks;
  delete globalThis.__noonSectorAPI;
}
