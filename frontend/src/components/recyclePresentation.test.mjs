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
