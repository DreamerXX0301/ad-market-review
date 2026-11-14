import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, fileName = "test.ts") =>
  ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
async function helper(path) {
  return import(
    encode(compile(await fs.readFile(new URL(path, import.meta.url), "utf8")))
  );
}
async function functionSource(path, name) {
  const source = await fs.readFile(new URL(path, import.meta.url), "utf8");
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const node = ast.statements.find(
    (n) => ts.isFunctionDeclaration(n) && n.name?.text === name,
  );
  assert.ok(node, `actual ${name} component exists`);
  return compile(
    node.getText(ast).replace(/^export (?:default )?/, ""),
    "component.tsx",
  )
    .replace(/^import .+;\n/gm, "")
    .replace(/export \{\};?\n?/g, "");
}
const tableCode = await functionSource("../../components/UI.tsx", "Table");
const stockCode = await functionSource(
  "./TrackingStockTable.tsx",
  "TrackingStockTable",
);
const singleSort = await helper("../../components/tableSort.ts");
const multipleSort = await helper("../../components/multiTableSort.ts");
const number = (value, digits = 2) =>
  value == null ? "—" : Number(value).toFixed(digits);
const percentage = (value) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}%`;
const StubIcon = () => jsx("span", { "data-icon": true });
const common = {
  _jsx: jsx,
  _jsxs: jsxs,
  _Fragment: React.Fragment,
  Fragment: React.Fragment,
  isHiddenIndicatorField: () => false,
  ...singleSort,
  ...multipleSort,
  label: {},
  num: number,
  percent: percentage,
  tone: (value) => (value > 0 ? "up" : value < 0 ? "down" : "muted"),
  Cell: ({ value }) =>
    jsx("span", { children: value == null ? "—" : String(value) }),
  Empty: ({ title }) => jsx("p", { children: title }),
  Badge: ({ children }) => jsx("span", { className: "badge", children }),
  CopyStockCode: ({ code }) =>
    jsx("span", { className: "copy-stock-code", children: code }),
  MultiSortControls: () => jsx("div", { "data-multi-sort": true }),
  ArrowDown: StubIcon,
  ArrowUp: StubIcon,
  ArrowUpDown: StubIcon,
  ChevronLeft: StubIcon,
  ChevronRight: StubIcon,
  ChevronDown: StubIcon,
};
function harness(code, name, extra = {}) {
  let states = [],
    cursor = 0;
  const scope = {
    ...common,
    ...extra,
    useState(initial) {
      const index = cursor++;
      if (!(index in states))
        states[index] = typeof initial === "function" ? initial() : initial;
      return [
        states[index],
        (value) => {
          states[index] =
            typeof value === "function" ? value(states[index]) : value;
        },
      ];
    },
    useEffect() {},
  };
  const component = Function(
    ...Object.keys(scope),
    `${code}\nreturn ${name};`,
  )(...Object.values(scope));
  return {
    render(props) {
      cursor = 0;
      return component(props);
    },
    reset() {
      states = [];
    },
  };
}
function walk(node, predicate) {
  if (Array.isArray(node))
    return node.flatMap((child) => walk(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [
    ...(predicate(node) ? [node] : []),
    ...walk(node.props.children, predicate),
  ];
}
const find = (node, predicate) => walk(node, predicate)[0];
const html = (node) => renderToStaticMarkup(node);

// Exercise the actual shared Table, with hook state isolated from any browser.
const table = harness(tableCode, "Table");
const parents = Array.from({ length: 5 }, (_, i) => ({
  instrument_id: `60000${i}.SH`,
  name: `股票${i}`,
  strategy_count: i + 1,
}));
const props = {
  rows: parents,
  rowKey: "instrument_id",
  columns: [{ key: "name" }, { key: "strategy_count" }],
  pageSize: 2,
  multiSort: true,
  multiDefaultSort: [{ key: "strategy_count", order: "desc" }],
};
let tree = table.render(props);
assert.equal(
  walk(tree, (n) => n.type === "tr").length,
  3,
  "legacy parent rows and header are unchanged",
);
assert.ok(
  walk(tree, (n) => n.type === "tr").every(
    (row) =>
      row.props.onClick === undefined && row.props.tabIndex === undefined,
  ),
  "legacy tables do not acquire clickable or focusable rows",
);
assert.doesNotMatch(html(tree), /table-expanded-row/);
const expandedCalls = [];
const withExpansion = {
  ...props,
  renderExpandedRow(row, ordered) {
    expandedCalls.push({ row, ordered });
    return row.strategy_count === 5
      ? jsx("div", { children: "五个独立轮次" })
      : null;
  },
};
tree = table.render(withExpansion);
assert.deepEqual(
  expandedCalls.map((v) => v.row.strategy_count),
  [5, 4],
  "only current-page parents ask for child content",
);
assert.deepEqual(
  expandedCalls[0].ordered.map((v) => v.strategy_count),
  [5, 4, 3, 2, 1],
  "child navigation receives the full parent sort before paging",
);
assert.equal(walk(tree, (n) => n.type === "tr").length, 4);
assert.equal(
  find(
    tree,
    (n) => n.type === "tr" && n.props.className === "table-expanded-row",
  ).props.children.props.colSpan,
  2,
);
assert.match(html(tree), /共 5 条/);
assert.match(html(tree), /1–2/);
find(tree, (n) => n.props["aria-label"] === "下一页").props.onClick();
expandedCalls.length = 0;
tree = table.render(withExpansion);
assert.deepEqual(
  expandedCalls.map((v) => v.row.strategy_count),
  [3, 2],
);
assert.doesNotMatch(
  html(tree),
  /五个独立轮次/,
  "expanded content cannot leak onto another parent's page",
);
assert.match(html(tree), /3–4/);
table.reset();
tree = table.render({ ...withExpansion, onSelect() {}, selection: new Set() });
assert.equal(
  find(
    tree,
    (n) => n.type === "tr" && n.props.className === "table-expanded-row",
  ).props.children.props.colSpan,
  3,
  "selection column is included in the full-width child row",
);

// The grouping component keeps chart navigation separate from expansion and
// never invents a shared entry price or strategy-specific action.
const TableMarker = () => null;
const panel = harness(stockCode, "TrackingStockTable", { Table: TableMarker });
const roundsA = [
  {
    round_id: "a-one",
    strategy_id: "one",
    entry_price: 10,
    tracking_change_pct: 20,
  },
  {
    round_id: "a-two",
    strategy_id: "two",
    entry_price: 14,
    tracking_change_pct: -10,
  },
];
const roundsB = [{ round_id: "b-one", strategy_id: "three", entry_price: 30 }];
const groups = [
  {
    instrument_id: "600001.SH",
    code: "600001",
    name: "甲股票",
    rounds: roundsA,
    strategy_count: 2,
    round_count: 2,
    strategy_names: ["策略一", "策略二"],
    latest_price: 12,
    today_change_pct: 2,
    valuation_date: "2026-11-13",
    earliest_entry_date: "2026-11-02",
    last_entry_date: "2026-11-11",
    triggered_round_count: 1,
  },
  {
    instrument_id: "600002.SH",
    code: "600002",
    name: "乙股票",
    rounds: roundsB,
    strategy_count: 1,
    round_count: 1,
    strategy_names: ["策略三"],
    latest_price: null,
    today_change_pct: null,
    valuation_date: null,
    quote_reason: "共同报价待核验",
    earliest_entry_date: "2026-11-05",
    last_entry_date: "2026-11-05",
    triggered_round_count: 0,
  },
];
const before = structuredClone(groups),
  roundCalls = [],
  chartCalls = [];
let panelProps = {
  groups,
  businessDate: "2026-11-13",
  hasFilters: false,
  onStockNavigate(id, orderedGroups, clickedGroup) {
    chartCalls.push({ id, orderedGroups, clickedGroup });
  },
  renderRoundTable(rounds, orderedGroups) {
    roundCalls.push({ rounds, orderedGroups });
    return jsx("div", {
      "data-child-rounds": true,
      children: rounds.map((r) => r.round_id).join(","),
    });
  },
};
function parentProps() {
  return find(panel.render(panelProps), (n) => n.type === TableMarker).props;
}
let actualTableProps = parentProps();
assert.equal(actualTableProps.rowKey, "instrument_id");
assert.equal(actualTableProps.pageSize, 20);
assert.equal(actualTableProps.rows, groups);
assert.equal(actualTableProps.onStockNavigate, panelProps.onStockNavigate);
assert.equal(
  actualTableProps.onStock,
  undefined,
  "parent stock navigation supplies the full group rather than an arbitrary strategy round",
);
assert.equal(
  actualTableProps.columns.find((c) => c.key === "name").render,
  undefined,
  "the stock name uses the shared chart-navigation and copy controls",
);
assert.equal(roundCalls.length, 0);
assert.ok(
  !actualTableProps.columns.some((c) =>
    ["tracking_change_pct", "entry_price", "policy", "trading_age"].includes(
      c.key,
    ),
  ),
);
const renderedParents = harness(tableCode, "Table");
tree = renderedParents.render({
  ...actualTableProps,
  rows: [...groups].reverse(),
});
assert.match(html(tree), /当前匹配 2 个策略/);
assert.match(html(tree), /各策略基准与跟踪涨幅见展开明细/);
assert.match(html(tree), /共同报价待核验/);
assert.doesNotMatch(html(tree), /a-one|a-two/);
const chartButton = find(
  stockRow(tree),
  (n) => n.type === "button" && n.props.className === "stock-link",
);
assert.ok(chartButton, "a stock name still opens its chart");
assert.equal(chartButton.props["aria-expanded"], undefined);
chartButton.props.onClick();
stockRow(tree).props.onClick(controlEvent(/\bbutton\b/));
assert.equal(chartCalls.length, 1);
assert.equal(chartCalls[0].id, groups[0].instrument_id);
assert.deepEqual(
  chartCalls[0].orderedGroups,
  groups,
  "stock charts receive all matching groups in the current table sort",
);
assert.equal(chartCalls[0].clickedGroup, groups[0]);
assert.equal(chartCalls[0].clickedGroup.rounds, roundsA);
tree = renderedParents.render(parentProps());
assert.equal(
  stockRow(tree).props["aria-expanded"],
  false,
  "chart navigation and its bubbled click do not expand the stock row",
);
assert.doesNotMatch(html(tree), /data-child-rounds/);
stockRow(tree).props.onClick(plainCellEvent());
actualTableProps = parentProps();
tree = renderedParents.render(actualTableProps);
assert.equal(roundCalls.length, 1);
assert.equal(roundCalls[0].rounds, roundsA);
assert.deepEqual(
  roundCalls[0].orderedGroups.map((g) => g.instrument_id),
  ["600001.SH", "600002.SH"],
);
assert.match(html(tree), /a-one,a-two/);
assert.match(html(tree), /当前匹配 2 个轮次/);
assert.match(html(tree), /入池基准、收益、政策和移出操作分别属于每个策略轮次/);
assert.equal(stockRow(tree).props["aria-expanded"], true);

// A search/strategy filter supplies the matching subset. An already expanded
// parent must show only that subset, never stale children from the last scope.
panelProps = {
  ...panelProps,
  groups: [
    {
      ...groups[0],
      rounds: [roundsA[1]],
      strategy_count: 1,
      round_count: 1,
      strategy_names: ["策略二"],
    },
  ],
  hasFilters: true,
};
tree = renderedParents.render(parentProps());
assert.match(html(tree), /a-two/);
assert.doesNotMatch(html(tree), /a-one/);
assert.equal(roundCalls.at(-1).rounds.length, 1);
stockRow(tree).props.onClick(plainCellEvent());
tree = renderedParents.render(parentProps());
assert.doesNotMatch(html(tree), /data-child-rounds/);
assert.deepEqual(
  groups,
  before,
  "view toggles never mutate round data or policy",
);

// Whole-row expansion keeps controls independent and never applies to the
// sibling detail row. Exercise the row props on the real shared Table so this
// also catches accidentally attaching the handlers to a surrounding tbody.
panel.reset();
renderedParents.reset();
panelProps = { ...panelProps, groups, hasFilters: false };
function renderGroupRows() {
  actualTableProps = parentProps();
  return renderedParents.render(actualTableProps);
}
function stockRow(rendered, id = "600001-SH") {
  const row = find(
    rendered,
    (n) =>
      n.type === "tr" &&
      n.props["aria-controls"] === `tracking-stock-rounds-${id}`,
  );
  assert.ok(row, "the actual parent tr receives stock-specific row props");
  return row;
}
function plainCellEvent() {
  return { target: { closest: () => null } };
}
function controlEvent(selectorPattern) {
  const control = {
    closest(selector) {
      assert.match(selector, selectorPattern);
      return control;
    },
  };
  return { target: control };
}

tree = renderGroupRows();
let row = stockRow(tree);
assert.equal(row.props.className, "tracking-stock-group-row");
assert.equal(row.props.tabIndex, 0);
assert.equal(row.props["aria-expanded"], false);
row.props.onClick(plainCellEvent());
tree = renderGroupRows();
row = stockRow(tree);
assert.equal(row.props["aria-expanded"], true);
assert.match(html(tree), /a-one,a-two/);
const detailRow = find(
  tree,
  (n) => n.type === "tr" && n.props.className === "table-expanded-row",
);
assert.equal(detailRow.props.onClick, undefined);
assert.equal(detailRow.props.onKeyDown, undefined);
assert.equal(detailRow.props.tabIndex, undefined);
row.props.onClick(plainCellEvent());
tree = renderGroupRows();
assert.equal(stockRow(tree).props["aria-expanded"], false);
assert.doesNotMatch(html(tree), /data-child-rounds/);

for (const [control, selectorPattern] of [
  ["chart, copy and expansion buttons", /\bbutton\b/],
  ["links", /(?:^|,\s*)a(?:,|$)/],
  ["inputs", /\binput\b/],
  ["selects", /\bselect\b/],
  ["textareas", /\btextarea\b/],
  ["labels", /\blabel\b/],
  ["custom buttons", /\[role=["']button["']\]/],
  ["custom checkboxes", /\[role=["']checkbox["']\]/],
  ["editable elements", /\[contenteditable\]/],
]) {
  stockRow(tree).props.onClick(controlEvent(selectorPattern));
  tree = renderGroupRows();
  assert.equal(
    stockRow(tree).props["aria-expanded"],
    false,
    `${control} keep their independent action instead of toggling the row`,
  );
}

// An explicit expand button still toggles once even when its click bubbles.
row = stockRow(tree);
const expandButton = find(
  row,
  (n) =>
    n.type === "button" && n.props.className?.includes("tracking-stock-expand"),
);
expandButton.props.onClick();
row.props.onClick(controlEvent(/\bbutton\b/));
tree = renderGroupRows();
assert.equal(stockRow(tree).props["aria-expanded"], true);
row = stockRow(tree);
find(
  row,
  (n) => n.type === "button" && n.props.className === "stock-link",
).props.onClick();
row.props.onClick(controlEvent(/\bbutton\b/));
tree = renderGroupRows();
assert.equal(chartCalls.length, 2);
assert.equal(chartCalls.at(-1).clickedGroup, groups[0]);
assert.equal(
  stockRow(tree).props["aria-expanded"],
  true,
  "opening a chart while expanded does not collapse the details",
);
stockRow(tree).props.onClick(controlEvent(/\bbutton\b/));
tree = renderGroupRows();
assert.equal(
  stockRow(tree).props["aria-expanded"],
  true,
  "copying while expanded does not collapse the details",
);

// Keyboard expansion belongs only to a focused parent row, not its controls.
function keyEvent(key, focusedRow = true) {
  const currentTarget = {};
  const event = {
    key,
    currentTarget,
    target: focusedRow ? currentTarget : {},
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
  return event;
}
let keyboardEvent = keyEvent("Enter");
stockRow(tree).props.onKeyDown(keyboardEvent);
tree = renderGroupRows();
assert.equal(stockRow(tree).props["aria-expanded"], false);
assert.equal(keyboardEvent.prevented, true);
keyboardEvent = keyEvent(" ");
stockRow(tree).props.onKeyDown(keyboardEvent);
tree = renderGroupRows();
assert.equal(stockRow(tree).props["aria-expanded"], true);
assert.equal(keyboardEvent.prevented, true);
for (const event of [
  keyEvent("ArrowDown"),
  keyEvent("Enter", false),
  keyEvent(" ", false),
]) {
  stockRow(tree).props.onKeyDown(event);
  tree = renderGroupRows();
  assert.equal(stockRow(tree).props["aria-expanded"], true);
  assert.equal(event.prevented, false);
}
assert.deepEqual(
  groups,
  before,
  "row interactions keep the strategy round data intact",
);
console.log(
  "tracking stock table: parent sorting/pagination, row and keyboard expansion, independent controls, matching-round scope and strategy baseline isolation passed",
);
