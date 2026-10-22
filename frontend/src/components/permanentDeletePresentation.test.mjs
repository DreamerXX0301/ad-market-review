import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./permanentDeletePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const helperUrl =
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64");
const {
  canConfirmPurge,
  hasWatchlistProtection,
  matchingPurgeStatus,
  purgeCount,
  purgeBytes,
  purgeScopeRows,
  purgeIsWorking,
  purgeBlocksRestore,
  recyclePageOffset,
} = await import(helperUrl);

const target = { kind: "strategy", id: "strategy-example", name: "测试策略" };
const preview = {
  target_kind: "strategy",
  target_id: target.id,
  name: target.name,
  can_purge: true,
  confirmation_token: "scope-token",
  confirmation_text: target.name,
  expires_at_ms: 2000,
  protected_watchlist_count: 0,
  protected_watchlist: [],
  counts: {
    strategies: 1,
    strategy_versions: 2,
    backtests: 3,
    tracking_rounds: 10,
    tracking_events: 17,
    selection_runs: 5,
    selection_candidates: 11,
    tracking_policies: 2,
    policy_applications: 10,
    tracking_ignores: 1,
    execution_settings: 1,
  },
  files: { exclusive_count: 4, exclusive_bytes: 2048, shared_count: 3 },
  retained: ["独立自选、划线、真实全市场行情与已有备份保留。"],
};
assert.equal(canConfirmPurge(preview, target, target.name, 1999), true);
for (const value of ["", "测试", " 测试策略", "测试策略 "])
  assert.equal(
    canConfirmPurge(preview, target, value, 1000),
    false,
    "confirmation text must be exact",
  );
assert.equal(
  canConfirmPurge(preview, target, target.name, 2000),
  false,
  "expiry must be strict",
);
for (const changes of [
  { can_purge: false },
  { can_purge: undefined },
  { confirmation_token: "" },
  { confirmation_token: null },
  { expires_at_ms: "2000" },
  { expires_at_ms: null },
  { confirmation_text: "" },
  { target_id: "later-strategy" },
  { target_kind: "backtest" },
  { protected_watchlist_count: 1 },
  { protected_watchlist: [{ instrument_id: "601999.SH" }] },
])
  assert.equal(
    canConfirmPurge({ ...preview, ...changes }, target, target.name, 1000),
    false,
  );
assert.equal(canConfirmPurge(null, target, target.name, 1000), false);
assert.equal(
  hasWatchlistProtection({ ...preview, protected_watchlist_count: 24 }),
  true,
);
assert.deepEqual(purgeScopeRows(preview), [
  ["策略", "1"],
  ["策略版本", "2"],
  ["回测记录", "3"],
  ["跟踪轮次", "10"],
  ["跟踪事件", "17"],
  ["选股运行", "5"],
  ["选股结果", "11"],
  ["跟踪规则", "2"],
  ["规则应用", "10"],
  ["跟踪忽略记录", "1"],
  ["执行设置", "1"],
]);
assert.deepEqual(purgeScopeRows({ ...preview, target_kind: "backtest" }), [
  ["回测记录", "3"],
]);
for (const unknown of [null, undefined, "0", false, -1, NaN, Infinity])
  assert.equal(purgeCount(unknown), "—");
assert.equal(purgeCount(0), "0");
assert.equal(purgeBytes(null), "—");
assert.equal(purgeBytes(0), "0 B");
assert.equal(purgeBytes(2048), "2.0 KiB");
assert.equal(
  matchingPurgeStatus({ purge_id: "old", status: "completed" }, "current"),
  null,
  "late job responses cannot announce another object complete",
);
assert.equal(
  matchingPurgeStatus(
    { purge_id: "current", status: "failed", business_deleted: true },
    "current",
  ).status,
  "failed",
);
assert.equal(purgeIsWorking("failed"), false);
assert.equal(purgeIsWorking("cleaning"), true);
assert.equal(purgeIsWorking("queued"), true);
assert.equal(
  purgeBlocksRestore({ status: "failed", business_deleted: false }),
  false,
  "a stopped, untouched object remains recoverable",
);
assert.equal(
  purgeBlocksRestore({ status: "failed", business_deleted: true }),
  true,
);
assert.equal(
  purgeBlocksRestore({ status: "queued", business_deleted: false }),
  true,
);
assert.equal(
  purgeBlocksRestore({ status: "cleaning", business_deleted: true }),
  true,
);
assert.equal(purgeBlocksRestore(null), false);
assert.equal(recyclePageOffset(50, 50), 25);
assert.equal(recyclePageOffset(0, 50), 0);
assert.equal(recyclePageOffset(53, 50), 50);
assert.equal(
  recyclePageOffset(undefined, 50),
  50,
  "unknown totals must not eject a retained page",
);
assert.equal(recyclePageOffset(20, 20, 20), 0);

console.log(
  "permanent deletion presentation: exact scoped confirmation, watchlist fail-closed, expiry, unknown counts, status identity and last-page adjustment passed",
);
