import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
    },
  }).outputText;
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
async function helper(path) {
  return import(
    encode(compile(await fs.readFile(new URL(path, import.meta.url), "utf8")))
  );
}
const {
  createStockNavigation,
  trackingEventBaseline,
  stockNavigationPosition,
  stockSelectionForOpen,
  stepStockSelection,
} = await helper("../../components/stockNavigation.ts");
const { groupTrackingStocks } = await helper("./trackingStockGroups.ts");
const source = await fs.readFile(
  new URL("./Tracking.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Tracking.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const component = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "StockTracking",
);
assert.ok(component, "the actual stock tracking component exists");
const declarations = new Map();
for (const statement of component.body.statements) {
  if (ts.isVariableStatement(statement))
    for (const node of statement.declarationList.declarations)
      declarations.set(node.name.getText(ast), node.initializer);
}
function evaluate(expression, scope) {
  return Function(
    ...Object.keys(scope),
    compile("const value = " + expression + ";") + "\nreturn value;",
  )(...Object.values(scope));
}
function collect(node, matches, selected = []) {
  if (matches(node)) selected.push(node);
  ts.forEachChild(node, (child) => {
    collect(child, matches, selected);
  });
  return selected;
}
const roundTable = declarations.get("renderRoundTable");
assert.ok(roundTable && ts.isArrowFunction(roundTable));
const roundNavigationAttributes = collect(
  roundTable,
  (node) =>
    ts.isJsxAttribute(node) && node.name.getText(ast) === "onStockNavigate",
);
assert.equal(roundNavigationAttributes.length, 1);
const navigationExpression =
  roundNavigationAttributes[0].initializer.expression;
assert.ok(ts.isConditionalExpression(navigationExpression));
const groupTable = collect(
  component,
  (node) =>
    ts.isJsxSelfClosingElement(node) &&
    node.tagName.getText(ast) === "TrackingStockTable",
)[0];
assert.ok(
  groupTable,
  "the aggregate stock table must use the real parent wiring",
);
const groupRender = groupTable.attributes.properties.find(
  (node) =>
    ts.isJsxAttribute(node) && node.name.getText(ast) === "renderRoundTable",
).initializer.expression;
assert.ok(ts.isArrowFunction(groupRender));
const openStockExpression = declarations.get("openStockFromList").getText(ast);
const businessDate = "2026-11-13";

function round(symbol, strategy, entry, date, name) {
  return {
    instrument_id: symbol,
    name,
    code: symbol.split(".")[0],
    strategy_id: strategy,
    strategy_name: strategy === "strategy-b" ? "策略乙" : "策略甲",
    round_id: `${strategy}:${symbol}`,
    status: "tracking",
    valuation_date: businessDate,
    latest_price: 12,
    today_change_pct: 2,
    entry_business_date: date,
    entry_price: entry,
    entry_basis: {
      price_basis: "none",
      entry_checks: [
        {
          field: "history_event",
          evidence: { key: `${strategy}:${symbol}`, entry },
        },
      ],
    },
    policy: { duration_sessions: strategy === "strategy-b" ? 5 : 30 },
    alerts: [],
  };
}
const a1 = round("600001.SH", "strategy-a", 10, "2026-10-20", "甲股");
const b1 = round("600001.SH", "strategy-b", 20, "2026-11-04", "甲股");
const a2 = round("600002.SH", "strategy-a", 30, "2026-10-21", "乙股");
const b2 = round("600002.SH", "strategy-b", 40, "2026-11-05", "乙股");
const a3 = round("600003.SH", "strategy-a", 60, "2026-10-22", "丙股");
const b3 = round("600003.SH", "strategy-b", 50, "2026-11-06", "丙股");
const aOnly = round("600004.SH", "strategy-a", 70, "2026-10-23", "丁股");
const rows = [a1, b1, a2, b2, a3, b3, aOnly];
const original = structuredClone(rows);
const groups = groupTrackingStocks(rows, businessDate);
const orderedGroups = [groups[2], groups[0], groups[3], groups[1]];
const expectedB = [b3, b1, b2];
const byRound = new Map(rows.map((row) => [row.round_id, row]));
let opened;
const openStockFromList = evaluate(openStockExpression, {
  byRound,
  createStockNavigation,
  trackingEventBaseline,
  tab: "active",
  strategyId: "",
  triggered: false,
  topicIds: [],
  topicMatch: "any",
  stockQuery: "",
  includeUnknown: false,
  hotFilter: { enabled: false },
  result: { data: { business_date: businessDate } },
  onStock(id, selectedRound, navigation) {
    opened = { id, round: selectedRound, navigation };
  },
});

// Exercise the real parent's aggregate render callback, not a hand-written
// flatten/filter approximation. The nested child table has a different local
// order, which must not replace the sorted parent-stock navigation order.
let childNavigationRows;
let childTableRows;
const renderGroupedChildren = evaluate(groupRender.getText(ast), {
  renderRoundTable(roundRows, navigationRows) {
    childTableRows = roundRows;
    childNavigationRows = navigationRows;
    return evaluate(navigationExpression.getText(ast), {
      navigationRows,
      openStockFromList,
    });
  },
});
const groupedHandler = renderGroupedChildren([a1, b1], orderedGroups);
assert.deepEqual(childTableRows, [a1, b1]);
assert.deepEqual(childNavigationRows, [a3, b3, a1, b1, aOnly, a2, b2]);
groupedHandler(b1.instrument_id, [b1, a1], b1);
assert.equal(
  opened.round,
  b1,
  "the clicked B round owns the opened chart basis",
);
assert.equal(opened.id, b1.instrument_id);
assert.equal(opened.navigation.asOf, businessDate);
assert.deepEqual(
  opened.navigation.items.map((item) => item.id),
  expectedB.map((row) => row.instrument_id),
  "aggregate navigation follows sorted parent stocks within clicked strategy B",
);
assert.deepEqual(
  opened.navigation.items.map((item) => item.eventBaseline.roundId),
  expectedB.map((row) => row.round_id),
);
assert.deepEqual(
  opened.navigation.items.map((item) => item.eventBaseline.rawClose),
  [50, 20, 40],
  "every B stock retains its own entry rather than any A round's price",
);
assert.deepEqual(
  opened.navigation.items.map((item) => item.eventBaseline.date),
  expectedB.map((row) => row.entry_business_date),
);
assert.deepEqual(
  opened.navigation.items.map((item) => item.historicalChecks),
  expectedB.map((row) => row.entry_basis.entry_checks),
);
assert.equal(JSON.parse(opened.navigation.scopeKey).strategyId, "");
assert.equal(
  JSON.parse(opened.navigation.scopeKey).navigationStrategyId,
  "strategy-b",
  "scope records the child strategy even when the global filter is all strategies",
);
assert.match(opened.navigation.label, /策略乙/);
assert.match(opened.navigation.label, /当前匹配股票/);
assert.doesNotMatch(opened.navigation.label, /策略甲/);

const groupSelection = {
  id: b1.instrument_id,
  asOf: businessDate,
  navigation: opened.navigation,
  eventBaseline: trackingEventBaseline(b1),
};
const previous = stepStockSelection(groupSelection, -1);
const next = stepStockSelection(groupSelection, 1);
assert.equal(previous.id, b3.instrument_id);
assert.equal(next.id, b2.instrument_id);
assert.equal("eventBaseline" in previous, false);
assert.equal("eventBaseline" in next, false);
assert.equal(
  stockNavigationPosition(previous.navigation, previous.id).current
    .eventBaseline.rawClose,
  50,
);
assert.equal(
  stockNavigationPosition(next.navigation, next.id).current.eventBaseline
    .rawClose,
  40,
);
assert.equal(
  stockNavigationPosition(next.navigation, next.id).current.eventBaseline
    .roundId,
  b2.round_id,
);

// The current round map is authoritative if a clicked display copy omits or
// carries stale detail fields. Neither another strategy nor the copy wins.
const displayCopy = {
  ...b1,
  entry_price: 999,
  entry_basis: { price_basis: "none", entry_checks: [{ wrong_copy: true }] },
};
groupedHandler(displayCopy.instrument_id, [displayCopy], displayCopy);
assert.equal(opened.round, b1);
const clickedBasis = opened.navigation.items.find(
  (item) => item.id === b1.instrument_id,
);
assert.equal(clickedBasis.eventBaseline.rawClose, 20);
assert.equal(clickedBasis.eventBaseline.roundId, b1.round_id);
assert.deepEqual(clickedBasis.historicalChecks, b1.entry_basis.entry_checks);

// Existing detail/history wiring uses the exact original callback and its
// table-provided order, rather than accidentally adopting grouped filtering.
const detailHandler = evaluate(navigationExpression.getText(ast), {
  navigationRows: undefined,
  openStockFromList,
});
assert.equal(detailHandler, openStockFromList);
const detailOrder = [a3, a1, b1, a2, b2, aOnly, b3];
detailHandler(b1.instrument_id, detailOrder, b1);
assert.deepEqual(
  opened.navigation.items.map((item) => item.id),
  [a3, a1, a2, aOnly].map((row) => row.instrument_id),
);
assert.deepEqual(
  opened.navigation.items.map((item) => item.eventBaseline.rawClose),
  [60, 20, 30, 70],
  "legacy detail navigation keeps original first-per-stock choice except clicked round preference",
);
assert.equal(JSON.parse(opened.navigation.scopeKey).navigationStrategyId, null);
assert.match(opened.navigation.label, /当前筛选/);
assert.doesNotMatch(opened.navigation.label, /策略乙/);
assert.equal(opened.round, b1);

assert.deepEqual(
  rows,
  original,
  "navigation never rewrites round prices or evidence",
);
assert.deepEqual(orderedGroups[1].rounds, [a1, b1]);
assert.deepEqual(childNavigationRows, [a3, b3, a1, b1, aOnly, a2, b2]);
assert.notEqual(
  opened.navigation.items[1].historicalChecks,
  b1.entry_basis.entry_checks,
  "drawer history is detached from live round evidence",
);

// The outer stock name is a chart entry, independent of every child round.
// Exercise its actual JSX wiring and callback with contaminated group objects
// so a future spread cannot accidentally select the first strategy's evidence.
const childOpened = opened;
const parentChartAttribute = groupTable.attributes.properties.find(
  (node) =>
    ts.isJsxAttribute(node) && node.name.getText(ast) === "onStockNavigate",
);
assert.ok(parentChartAttribute, "the outer group table exposes chart browsing");
const parentChartName = parentChartAttribute.initializer.expression;
assert.ok(ts.isIdentifier(parentChartName));
const parentChartExpression = declarations.get(parentChartName.text);
assert.ok(parentChartExpression && ts.isArrowFunction(parentChartExpression));
const contaminatedGroups = orderedGroups.map((group) => ({
  ...group,
  round_id: "wrong-parent-round",
  entry_price: 999,
  eventBaseline: trackingEventBaseline(group.rounds[0]),
  historicalChecks: [{ wrong_parent_evidence: true }],
  checks: [{ wrong_parent_condition: true }],
  entry_basis: { entry_checks: [{ wrong_parent_basis: true }] },
}));
const untouchedGroups = structuredClone(contaminatedGroups);
const parentChartHandler = evaluate(parentChartExpression.getText(ast), {
  createStockNavigation,
  result: { data: { business_date: businessDate } },
  onStock(id, selectedRound, navigation) {
    opened = { id, round: selectedRound, navigation };
  },
});
parentChartHandler(b1.instrument_id, contaminatedGroups, contaminatedGroups[1]);
const parentOpened = opened;
assert.equal(parentOpened.id, b1.instrument_id);
assert.equal(parentOpened.round, null, "the outer chart has no explicit round");
assert.equal(parentOpened.navigation.source, "tracking");
assert.equal(parentOpened.navigation.asOf, businessDate);
assert.match(parentOpened.navigation.label, /股票汇总/);
assert.deepEqual(
  parentOpened.navigation.items.map((item) => item.id),
  orderedGroups.map((group) => group.instrument_id),
  "outer chart navigation preserves the sorted parent stocks before pagination",
);
assert.deepEqual(
  parentOpened.navigation.items,
  orderedGroups.map((group) => ({
    id: group.instrument_id,
    name: group.name,
    code: group.code,
  })),
  "outer chart items contain identity only, without arbitrary strategy evidence",
);
const parentScope = JSON.parse(parentOpened.navigation.scopeKey);
assert.equal(parentScope.view, "stocks");
assert.equal(parentScope.businessDate, businessDate);
assert.deepEqual(
  parentScope.ids,
  orderedGroups.map((group) => group.instrument_id),
);
assert.deepEqual(contaminatedGroups, untouchedGroups);

// Follow the real App callback into a fresh drawer selection. Returning to an
// overview after a concrete child chart must clear its explicit event baseline;
// the shared drawer's existing event-reference picker still owns its defaults.
const appSource = await fs.readFile(
  new URL("../../App.tsx", import.meta.url),
  "utf8",
);
const appAst = ts.createSourceFile(
  "App.tsx",
  appSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const appComponent = appAst.statements.find(
  (node) => ts.isFunctionDeclaration(node) && node.name?.text === "App",
);
assert.ok(appComponent);
const appDeclarations = new Map();
for (const statement of appComponent.body.statements) {
  if (ts.isVariableStatement(statement))
    for (const node of statement.declarationList.declarations)
      appDeclarations.set(node.name.getText(appAst), node.initializer);
}
const trackedStockExpression = appDeclarations.get("openTrackedStock");
assert.ok(trackedStockExpression);
const appTrackingView = collect(
  appComponent,
  (node) =>
    ts.isJsxSelfClosingElement(node) &&
    node.tagName.getText(appAst) === "Tracking",
)[0];
const appTrackingHandoff = appTrackingView?.attributes.properties.find(
  (node) => ts.isJsxAttribute(node) && node.name.getText(appAst) === "onStock",
);
assert.equal(
  appTrackingHandoff?.initializer.expression.getText(appAst),
  "openTrackedStock",
  "the real tracking view uses the nullable App chart callback",
);
let drawerSelection;
const openTrackedStock = evaluate(trackedStockExpression.getText(appAst), {
  useCallback: (callback) => callback,
  stockSelectionForOpen,
  trackingEventBaseline,
  setStock(selection) {
    drawerSelection = selection;
  },
});
openTrackedStock(childOpened.id, childOpened.round, childOpened.navigation);
assert.equal(drawerSelection.eventBaseline.roundId, b1.round_id);
assert.equal(drawerSelection.eventBaseline.rawClose, b1.entry_price);
openTrackedStock(parentOpened.id, parentOpened.round, parentOpened.navigation);
assert.equal(drawerSelection.asOf, businessDate);
assert.equal(drawerSelection.navigation, parentOpened.navigation);
assert.equal("eventBaseline" in drawerSelection, false);
assert.equal("historicalChecks" in drawerSelection, false);
for (const [direction, expected] of [
  [-1, orderedGroups[0]],
  [1, orderedGroups[2]],
]) {
  const stepped = stepStockSelection(drawerSelection, direction);
  assert.equal(stepped.id, expected.instrument_id);
  assert.equal(stepped.asOf, businessDate);
  assert.equal("eventBaseline" in stepped, false);
  assert.equal("historicalChecks" in stepped, false);
  const item = stockNavigationPosition(stepped.navigation, stepped.id).current;
  assert.equal("eventBaseline" in item, false);
  assert.equal("historicalChecks" in item, false);
}
console.log(
  "tracking grouped navigation: outer chart identity/date/order, nullable App handoff, child strategy baselines, previous/next stocks and legacy details passed",
);
