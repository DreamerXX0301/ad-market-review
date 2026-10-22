import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(await fs.readFile(new URL("./storagePresentation.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { storageSize, publishedStorageView: view } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
for (const value of [null, undefined, "1000000000", -1, 1.5, true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) assert.deepEqual(storageSize(value), { gb: "—", gib: "—", bytes: "—" });
assert.deepEqual(storageSize(0), { gb: "0.000 GB", gib: "0.000 GiB", bytes: "0 B" });
assert.equal(storageSize(1).gb, "<0.001 GB", "positive small files never round down to an apparent zero");
assert.equal(storageSize(1).bytes, "1 B");
assert.equal(storageSize(1_000_000_000).gb, "1.000 GB");
assert.equal(storageSize(1_000_000_000).gib, "0.931 GiB", "decimal and binary units must not be renamed interchangeably");
assert.equal(storageSize(1_073_741_824).gb, "1.074 GB");
assert.equal(storageSize(1_073_741_824).gib, "1.000 GiB");
const categoryValues = [235751787, 939555705, 480119100, 9462439, 734245];
const keys = ["current_market", "old_market", "supplement", "backtests", "metadata_other"];
const accounting = {
  scope: "active_published_root", logical_bytes: 1665623276, allocated_bytes: null, complete: true, file_count: 15,
  categories: keys.map((key, index) => ({ key, logical_bytes: categoryValues[index], file_count: index + 1, ...(index < 2 ? { generation_count: index ? 4 : 1 } : {}) })),
  excluded: { staging_entries: 2, symlink_entries: 1, special_entries: 0 },
};
const summary = { storage_bytes: accounting.logical_bytes, storage_accounting: accounting };
const before = JSON.stringify(summary);
const complete = view(summary);
assert.equal(complete.valid, true); assert.equal(complete.complete, true);
assert.equal(complete.value, "1.666 GB"); assert.equal(complete.size.bytes, "1,665,623,276 B");
assert.deepEqual(complete.rows.map(row => row.key), keys);
assert.equal(complete.rows[1].generations, "4");
assert.equal(complete.files, "15"); assert.equal(complete.allocated, null, "missing block accounting cannot mean zero disk usage");
assert.deepEqual(complete.omitted, ["临时项 2 项", "符号链接 1 项"]);
assert.equal(JSON.stringify(summary), before, "rendering never changes accounting or initiates cleanup");
const partial = view({ ...summary, storage_accounting: { ...accounting, complete: false, reason: "一个目录暂不可读" } });
assert.equal(partial.complete, false); assert.match(partial.sub, /已计量小计/); assert.equal(partial.reason, "一个目录暂不可读");
const legacy = view({ storage_bytes: 1_000_000_000 });
assert.equal(legacy.value, "1.000 GB"); assert.equal(legacy.complete, null); assert.deepEqual(legacy.rows, [], "older API totals never invent a breakdown");
assert.equal(view({}).value, "—");
for (const bad of [
  { ...accounting, scope: "whole_machine" },
  { ...accounting, categories: accounting.categories.slice(0, 4) },
  { ...accounting, categories: [...accounting.categories.slice(0, 4), accounting.categories[0]] },
  { ...accounting, logical_bytes: accounting.logical_bytes + 1 },
  { ...accounting, file_count: 20 },
]) {
  const result = view({ storage_bytes: accounting.logical_bytes, storage_accounting: bad });
  assert.equal(result.valid, false); assert.equal(result.value, "—"); assert.deepEqual(result.rows, []);
}
const mismatch = view({ ...summary, storage_bytes: accounting.logical_bytes + 1 });
assert.equal(mismatch.valid, false, "contradictory totals are not silently reconciled by the browser");
console.log("published storage: decimal GB, explicit GiB/exact bytes, reconciled scope/categories, partial subtotal and missing metadata passed");
