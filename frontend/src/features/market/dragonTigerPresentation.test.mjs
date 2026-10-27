import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = ts.transpileModule(await fs.readFile(new URL("./dragonTigerPresentation.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const helpers = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const { dragonTigerRows, filterDragonTigerRows, sortDragonTigerRows, dragonTigerSummary, dragonTigerLabels, uniqueDragonTigerStocks, dragonTigerDisclosure, dragonTigerJobId } = helpers;
// Synthetic data is limited to tests; no real collected response is embedded.
const stock = (id, name, fields = {}) => ({ instrument_id: id, code: id.slice(0, 6), name, range_days: 1, change_pct: 2.35, net_value: 100, buy_value: 150, sell_value: 50, org_net_value: 10, source_concepts: [{ name: "合成概念" }], ...fields });
const a = stock("600001.SH", "合成甲"), b = stock("600002.SH", "合成乙", { net_value: -50, buy_value: 20, sell_value: 70, org_net_value: null });
const packet = { boards: {
  all: { state: "ready", items: [a, b, { ...a, range_days: 3, net_value: 999 }, stock("unverified", "未核验", { code: "600003" })] },
  org: { state: "ready", items: [a, b] },
  hot_money: { state: "ready", items: [{ name: "合成标签A", rows: [{ ...a, label_net_value: 25 }, { ...b, label_net_value: -10 }] }, { name: "合成标签B", rows: [{ ...a, label_net_value: 30 }] }] },
} };
const original = structuredClone(packet);
const all = dragonTigerRows(packet, "all");
assert.equal(all[0].change_pct, 2.35, "the API already supplies percentage points");
assert.equal(all[3].instrument_id, null, "code/name cannot establish a missing verified identity");
assert.equal(filterDragonTigerRows(all, 1, "", null).length, 2);
assert.equal(filterDragonTigerRows(all, 3, "", null)[0].net_value, 999, "single and cumulative disclosures remain separate");
assert.equal(filterDragonTigerRows(all, 1, " 合成甲 ", null).length, 1);
assert.equal(filterDragonTigerRows(all, 1, "合成概念", null).length, 2);
assert.equal(filterDragonTigerRows(all, 1, "600002", null)[0].name, "合成乙");
const hot = filterDragonTigerRows(dragonTigerRows(packet, "hot_money"), 1, "", null);
assert.equal(hot.length, 3);
assert.equal(uniqueDragonTigerStocks(hot).length, 2);
assert.deepEqual(dragonTigerSummary(hot, "hot_money", null), { count: 2, net: 50, netLabel: "股票龙虎榜净额（去重）", buy: 170, sell: 120 });
assert.equal(dragonTigerSummary(hot, "hot_money", null).buy, 170, "same-stock amounts must not repeat for multiple tags");
const tagged = filterDragonTigerRows(hot, 1, "", "合成标签A");
assert.equal(dragonTigerSummary(tagged, "hot_money", "合成标签A").net, 15, "selected-tag net uses label_net_value, not the stock net");
assert.equal(dragonTigerSummary(filterDragonTigerRows(all, 1, "", null), "org", null).net, null, "one unknown institution value keeps the aggregate unknown");
assert.deepEqual(dragonTigerLabels(hot, 1), [{ name: "合成标签A", count: 2 }, { name: "合成标签B", count: 1 }]);
const conflict = hot.map(row => ({ ...row })); conflict[2].buy_value = 151;
assert.equal(dragonTigerSummary(conflict, "hot_money", null).buy, null, "inconsistent repeated stock values are not silently selected or summed");
assert.deepEqual(dragonTigerSummary([], "all", null, false), { count: null, net: null, netLabel: "龙虎榜净买额", buy: null, sell: null });
assert.deepEqual(dragonTigerSummary([], "all", null, true), { count: 0, net: 0, netLabel: "龙虎榜净买额", buy: 0, sell: 0 }, "verified empty results differ from unavailable data");

const sortable = dragonTigerRows({ boards: { all: { items: [stock("600001.SH", "合成2", { net_value: 2, buy_value: 2 }), stock("600002.SH", "合成10", { net_value: -1, buy_value: 0 }), stock("600003.SH", null, { net_value: null, buy_value: NaN }), stock("600004.SH", "合成3", { net_value: 5, buy_value: 5 })] } } }, "all");
for (const key of ["name", "net", "buy", "org", "sell", "change"]) for (const order of ["asc", "desc"]) {
  const result = sortDragonTigerRows(sortable, { key, order }, "all");
  assert.deepEqual(new Set(result.map(row => row.key)), new Set(sortable.map(row => row.key)), "sorting preserves stable selection identities");
}
assert.equal(sortDragonTigerRows(sortable, { key: "net", order: "asc" }, "all").at(-1).net_value, null);
assert.equal(sortDragonTigerRows(sortable, { key: "net", order: "desc" }, "all").at(-1).net_value, null);
assert.equal(sortDragonTigerRows(sortable, { key: "name", order: "asc" }, "all").at(-1).name, null);
assert.equal(sortDragonTigerRows(sortable, { key: "name", order: "desc" }, "all").at(-1).name, null);
assert.equal(sortDragonTigerRows(sortable, { key: "buy", order: "asc" }, "all")[0].buy_value, 0, "a genuine zero remains sortable");
const many = dragonTigerRows({ boards: { all: { items: Array.from({ length: 60 }, (_, index) => stock(String(600001 + index) + ".SH", `合成${index}`, { net_value: index })) } } }, "all");
const ordered = sortDragonTigerRows(many, { key: "net", order: "desc" }, "all");
assert.deepEqual(ordered.slice(0, 25).map(row => row.net_value), Array.from({ length: 25 }, (_, index) => 59 - index), "full results are sorted before the first page is sliced");
const selected = many[3].key;
assert.equal(ordered.find(row => row.key === selected).instrument_id, many[3].instrument_id);
assert.equal(Math.floor(ordered.findIndex(row => row.key === selected) / 25) + 1, 3, "return-to-row can restore the selected record's current page");
const records = [{ record_id: "synthetic-a", reason: "合成原因A" }, { record_id: "synthetic-b", reason: "合成原因B" }];
assert.equal(dragonTigerDisclosure(records, "synthetic-b").reason, "合成原因B");
assert.equal(dragonTigerDisclosure(records, "missing").record_id, "synthetic-a");
assert.equal(dragonTigerDisclosure(null, null), null);
assert.equal(dragonTigerJobId({ task_id: "synthetic-job" }), "synthetic-job");
assert.equal(dragonTigerJobId({ job_id: "synthetic-worker-job" }), "synthetic-worker-job");
assert.equal(dragonTigerJobId(null), null);
assert.deepEqual(packet, original, "normalizing and summarizing cannot mutate retained source evidence");
console.log("dragon tiger: source percentage, dated ranges, verified identities, label toggles, unique-stock totals, unknown values and stable full-result sorting passed");
