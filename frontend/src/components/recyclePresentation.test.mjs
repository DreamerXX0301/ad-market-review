import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(
    new URL("./recyclePresentation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  visibleRecycleRows,
  selectionAfterRecycle,
  recycleListWithout,
  deletionReason,
  restorationReason,
  selectedBacktestReport,
  selectedBacktestTrade,
  backtestSelectionForStrategies,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const a = Object.freeze({ backtest_id: "a", deleted: false }),
  b = Object.freeze({ backtest_id: "b", deleted: false });
const rows = Object.freeze([a, b]);
assert.equal(selectionAfterRecycle("a", "a", rows, "backtest_id"), "b");
assert.equal(
  selectionAfterRecycle("b", "a", rows, "backtest_id"),
  "b",
  "late delete result must preserve the newly selected report",
);
assert.equal(selectionAfterRecycle("a", "a", [a], "backtest_id"), "");
assert.equal(selectedBacktestReport(a, "a", false), a);
assert.equal(selectedBacktestReport(a, "b", false), null);
assert.equal(selectedBacktestReport(a, "a", true), null);
assert.equal(selectedBacktestReport({ ...a, deleted: true }, "a", false), null);
assert.equal(
  selectedBacktestReport(a, "a", false, "回测不存在"),
  null,
  "retained useApi data after a 404 must not appear as a valid report",
);
const restored = { ...b, deleted: false };
assert.deepEqual(visibleRecycleRows([restored], false, "backtest_id"), [
  restored,
]);
const cascaded = { ...b, deleted: true };
assert.deepEqual(
  visibleRecycleRows([cascaded], true, "backtest_id"),
  [cascaded],
  "a later server cascade must supersede an earlier manual restore in a retained page",
);
assert.deepEqual(visibleRecycleRows([cascaded], false, "backtest_id"), []);
const page = Object.freeze({ items: rows, total: 102, has_more: true });
assert.deepEqual(recycleListWithout(page, "a", "backtest_id"), {
  items: [b],
  total: 101,
  has_more: true,
});
assert.equal(page.items.length, 2);
assert.equal(page.total, 102);
assert.equal(recycleListWithout(page, "missing", "backtest_id").total, 102);
assert.equal(
  deletionReason({ calculation_status: "running", can_delete: true }),
  null,
  "server accounts for cancelled job and released lease",
);
assert.equal(
  deletionReason({ can_delete: false, delete_blocked_reason: "任务运行中" }),
  "任务运行中",
);
assert.equal(
  restorationReason({
    can_restore: false,
    restore_blocked_reason: "请先恢复所属策略",
  }),
  "请先恢复所属策略",
);
assert.equal(restorationReason({ can_restore: true }), null);
console.log(
  "recycle presentation: late selection isolation, authoritative server tombstones after cascade, pagination and restore gates passed",
);

const strategyRows = Object.freeze([
  { strategy_id: "live" },
  { strategy_id: "archived", archived: true },
]);
const gone = Object.freeze({
  backtest_id: "gone-report",
  strategy_id: "deleted-parent",
});
const archived = Object.freeze({
  backtest_id: "archived-report",
  strategy_id: "archived",
});
const live = Object.freeze({ backtest_id: "live-report", strategy_id: "live" });
assert.equal(
  backtestSelectionForStrategies(
    "gone-report",
    gone,
    [gone, archived, live],
    strategyRows,
    true,
  ),
  "archived-report",
  "a missing parent selects the first report whose parent still exists, including archived parents",
);
assert.equal(
  backtestSelectionForStrategies(
    "gone-report",
    gone,
    [gone],
    strategyRows,
    true,
  ),
  "",
  "cascade deletion clears selection when the current page has no surviving report",
);
assert.equal(
  backtestSelectionForStrategies("", null, [gone, live], strategyRows, true),
  "live-report",
  "new history can select its first surviving report",
);
assert.equal(
  backtestSelectionForStrategies(
    "off-page",
    { backtest_id: "off-page", strategy_id: "live" },
    [gone],
    strategyRows,
    true,
  ),
  "off-page",
  "pagination is never evidence that a legal selected report was deleted",
);
assert.equal(
  backtestSelectionForStrategies("unknown", null, [live], strategyRows, true),
  "unknown",
  "a failed detail read with unknown parent retains its selection and error",
);
assert.equal(
  backtestSelectionForStrategies(
    "live-report",
    live,
    [gone],
    strategyRows,
    true,
  ),
  "live-report",
  "network errors for an existing parent must not silently choose another report",
);
assert.equal(
  backtestSelectionForStrategies(
    "live-report",
    gone,
    [live],
    strategyRows,
    true,
  ),
  "live-report",
  "a stale detail response belonging to another id cannot clear the new selection",
);
assert.equal(
  backtestSelectionForStrategies(
    "gone-report",
    null,
    [gone, live],
    strategyRows,
    true,
  ),
  "live-report",
  "history metadata can prove the selected parent is gone even before detail succeeds",
);
assert.equal(
  backtestSelectionForStrategies(
    "archived-report",
    archived,
    [live],
    strategyRows,
    true,
  ),
  "archived-report",
);
assert.equal(
  backtestSelectionForStrategies(
    "gone-report",
    gone,
    [live],
    strategyRows,
    false,
  ),
  "gone-report",
  "loading/error strategy lists are not authoritative evidence",
);
assert.equal(
  backtestSelectionForStrategies("gone-report", gone, [], [], true),
  "",
  "a completed empty catalog can prove removal",
);
console.log(
  "cascade selection: full parent catalog authority, archived/off-page reports, delayed details, true network errors and first surviving report passed",
);

const oldTrade = Object.freeze({
  backtestId: "gone-report",
  trade: Object.freeze({ code: "old-trade" }),
});
const newTrade = Object.freeze({
  backtestId: "live-report",
  trade: Object.freeze({ code: "new-trade" }),
});
assert.equal(
  selectedBacktestTrade(oldTrade, "live-report", live),
  null,
  "automatic report changes cannot display the departed report's transaction dialog",
);
assert.equal(
  selectedBacktestTrade(newTrade, "live-report", live),
  newTrade.trade,
  "new report transaction details remain usable",
);
assert.equal(
  selectedBacktestTrade(newTrade, "live-report", gone),
  null,
  "a mismatched late report never exposes another report's trade",
);
assert.equal(
  selectedBacktestTrade(newTrade, "live-report", null),
  null,
  "unknown/failed report details do not expose transaction dialogs",
);
