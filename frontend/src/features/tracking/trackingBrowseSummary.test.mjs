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
const { createStockNavigation, trackingEventBaseline, stepStockSelection } =
  await helper("../../components/stockNavigation.ts");
const {
  trackingProgress,
  trackingTopicMatch,
  topicSortValue,
  matchesTrackingText,
} = await helper("./trackingFilterLogic.ts");
const { trackingPriceContext } = await helper("./trackingPricePresentation.ts");
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
for (const [tab, triggered, status, only] of [
  ["active", false, "tracking", "false"],
  ["active", true, "tracking", "true"],
  ["history", true, "all", "false"],
  ["alerts", true, "all", "false"],
]) {
  const query = evaluate(declarations.get("query").getText(ast), {
    tab,
    triggered,
  });
  assert.equal(query.get("view"), "summary");
  assert.equal(query.get("status"), status);
  assert.equal(query.get("triggered_only"), only);
}
const count = (events, rounds) =>
  evaluate(declarations.get("alertCount").getText(ast), {
    eventsResult: { data: events },
    result: { data: rounds },
  });
assert.equal(
  count(null, { counts: { alerts: 17 } }),
  17,
  "non-alert tabs retain the exact total without an event-list GET",
);
assert.equal(
  count({ total: 0 }, { counts: { alerts: 17 } }),
  0,
  "a fresh zero is not replaced by a previous list count",
);
assert.equal(count(null, null), undefined, "unread totals stay unknown");

// The explicit browse payload keeps all navigation/policy/filter evidence.
// Omitted blobs are available only from the already existing detail GET.
const policy = {
  duration_sessions: 45,
  alerts: [{ field: "close_below_entry", value: true }],
  exits: [],
  alert_logic: "OR",
  exit_logic: "OR",
  policy_version: 3,
  custom_future_field: "preserved",
};
const checks = [
  {
    field: "historical_event",
    matched: true,
    evidence: { event_date: "2026-10-12", event_price: 9.5 },
  },
];
const summary = {
  instrument_id: "600001.SH",
  name: "甲",
  code: "600001",
  round_id: "round-one",
  strategy_id: "strategy-one",
  strategy_name: "条件A",
  status: "tracking",
  entry_business_date: "2026-10-12",
  entry_price: 10,
  entry_basis: {
    price_basis: "none",
    signal_price_basis: "forward",
    entry_checks: checks,
  },
  policy,
  trading_age: 8,
  remaining_sessions: 37,
  today_change_pct: 2,
  tracking_change_pct: 4,
  valuation_date: "2026-10-27",
  tracking_reference_price_basis: "forward_event_affine",
  tracking_comparison_anchor: "2026-10-27",
  tracking_reference_price: 9.7,
  tracking_reference_evidence: {
    state: "ready",
    source_lineage: [{ sha256: "proof" }],
  },
  current_topics: [{ topic_id: "t", name: "当前题材" }],
  current_topics_observed_date: "2026-10-27",
  latest_event: { event_type: "alert", recorded_at_ms: 123 },
  alerts: [{ event_id: "event-one" }],
};
const full = {
  ...summary,
  entry_basis: {
    ...summary.entry_basis,
    source_lineage: [{ sha256: "raw" }],
    entry_topics: [{ topic_id: "old" }],
    entry_high: { basis: "raw" },
    entry_low: { basis: "raw" },
  },
  topics: [{ topic_id: "old" }],
  topics_snapshot: [{ topic_id: "old" }],
  entry_high_basis: { proof: "full" },
  entry_low_basis: { proof: "full" },
  latest_event: {
    ...summary.latest_event,
    evidence: { large: "only in detail" },
  },
  alerts: [{ ...summary.alerts[0], evidence: { large: "only in detail" } }],
};
for (const fn of [
  trackingEventBaseline,
  trackingProgress,
  trackingPriceContext,
  topicSortValue,
])
  assert.deepEqual(fn(summary), fn(full));
assert.equal(trackingTopicMatch(summary, ["t"], "any", new Set()), "matched");
assert.equal(
  trackingTopicMatch(summary, ["missing"], "all", new Set()),
  "unknown",
);
assert.equal(matchesTrackingText(summary, "条件A"), true);
assert.equal(
  trackingPriceContext({
    ...summary,
    tracking_reference_evidence: { state: "unknown" },
  }).change,
  null,
);

const policyComponent = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "PolicyEditor",
);
const policyInitial = policyComponent.body.statements
  .flatMap((node) =>
    ts.isVariableStatement(node) ? [...node.declarationList.declarations] : [],
  )
  .find((node) => node.name.getText(ast) === "initial")
  .initializer.getText(ast);
assert.deepEqual(
  evaluate(policyInitial, {
    row: summary,
    strategy: { policy: { duration_sessions: 30 } },
  }),
  policy,
  "custom round policy retains every field and wins over defaults",
);

const navigationFn = declarations.get("openStockFromList").getText(ast);
function navigate(row) {
  let opened;
  const second = {
    ...row,
    instrument_id: "600002.SH",
    round_id: "round-two",
    entry_price: 20,
    entry_basis: {
      ...row.entry_basis,
      entry_checks: [{ field: "second-event" }],
    },
  };
  const ordered = [
    second,
    row,
    { ...row, round_id: "older-round", entry_price: 8 },
  ];
  const scope = {
    byRound: new Map([...ordered].map((value) => [value.round_id, value])),
    createStockNavigation,
    trackingEventBaseline,
    tab: "active",
    strategyId: "strategy-one",
    triggered: false,
    topicIds: ["t"],
    topicMatch: "any",
    stockQuery: "",
    includeUnknown: true,
    hotFilter: {
      enabled: true,
      applied: { window_sessions: 5, top_n: 3 },
      mode: "observed",
      includeUnknown: true,
    },
    result: {
      data: {
        business_date: "2026-10-27",
        hot_filter_scope: {
          source_snapshot_id: "snapshot",
          topics_revision: "revision",
        },
      },
    },
    onStock(id, round, navigation) {
      opened = { id, round, navigation };
    },
  };
  evaluate(navigationFn, scope)(row.instrument_id, ordered, row);
  return opened;
}
const before = navigate(full),
  after = navigate(summary);
assert.deepEqual(
  after.navigation,
  before.navigation,
  "summary preserves filtered order, duplicate-round choice, full historical checks and per-stock event baselines",
);
assert.deepEqual(
  after.navigation.items.map((row) => row.id),
  ["600002.SH", "600001.SH"],
);
assert.equal(after.navigation.items[1].eventBaseline.rawClose, 10);
assert.deepEqual(after.navigation.items[1].historicalChecks, checks);
assert.equal(after.navigation.asOf, "2026-10-27");
assert.deepEqual(JSON.parse(after.navigation.scopeKey).hot.scope, {
  source_snapshot_id: "snapshot",
  topics_revision: "revision",
});
const next = stepStockSelection(
  {
    id: "600001.SH",
    navigation: after.navigation,
    eventBaseline: trackingEventBaseline(summary),
  },
  -1,
);
assert.equal(next.id, "600002.SH");
assert.equal("eventBaseline" in next, false);
checks[0].evidence.event_price = 999;
assert.equal(
  after.navigation.items[1].historicalChecks[0].evidence.event_price,
  9.5,
  "navigation evidence remains a detached snapshot",
);
assert.match(
  source,
  /api\(`\/tracking-rounds\/\$\{row.round_id\}`\)/,
  "explicit detail continues to use the full response",
);
console.log(
  "tracking browse summary preserves navigation, policy, prices, topic filters, scope and alert totals",
);
