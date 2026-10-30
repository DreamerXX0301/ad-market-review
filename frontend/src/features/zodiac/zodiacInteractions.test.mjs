import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./zodiacInteractions.ts", import.meta.url),
  "utf8",
);
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  nextColumnSort,
  poolSort,
  sortableFields,
  toggleStockSelection,
  additionNotice,
} = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
for (const key of [
  "change_pct",
  "return_ytd_pct",
  "change_30d_pct",
  "limit_up_ytd",
  "last_limit_up_date",
  "since_entry_pct",
]) {
  assert.ok(sortableFields.has(key));
  assert.equal(nextColumnSort("focused:desc", key), `${key}:desc`);
  assert.equal(nextColumnSort(`${key}:desc`, key), `${key}:asc`);
  assert.equal(nextColumnSort(`${key}:asc`, key), `${key}:desc`);
}
assert.equal(sortableFields.has("current_topics"), false);
assert.equal(poolSort("change_pct:desc", "change_pct:asc"), "change_pct:desc");
assert.equal(
  poolSort("change_pct:asc", "focused:desc"),
  "change_pct:asc,focused:desc",
);
const a = { instrument_id: "002724.SZ", name: "海洋王" },
  b = { instrument_id: "300053.SZ", name: "航宇微" };
let picked = toggleStockSelection([], a);
picked = toggleStockSelection(picked, b);
assert.deepEqual(picked, [a, b]);
assert.deepEqual(
  toggleStockSelection(picked, { ...a, name: "同一证券更新名称" }),
  [b],
);
assert.equal(
  toggleStockSelection(picked, { instrument_id: "600001.SH" }, 2),
  picked,
);
assert.equal(
  additionNotice({
    items: [
      { state: "added" },
      { state: "readded" },
      { state: "already_active" },
    ],
  }),
  "已加入 2 只；1 只已在池内，原信息保留",
);
assert.equal(
  additionNotice({ items: [{ state: "already_active" }] }),
  "已加入 0 只；1 只已在池内，原信息保留",
);
console.log(
  "生肖交互：连续多选、取消去重、容量边界、真实回执与全池排序参数通过",
);
