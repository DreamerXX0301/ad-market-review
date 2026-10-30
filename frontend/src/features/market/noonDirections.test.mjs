import assert from "node:assert/strict";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// Synthetic data exercises presentation only; no business snapshot or network writes.
const snapshotId = "noon_" + "a".repeat(32);
const industry = { instrument_id: "881001.TI", kind: "industry", name: "合成同名", change_pct: 2 };
const concept = { instrument_id: "885001.TI", kind: "concept", name: "合成同名", change_pct: 1 };
const unknown = { instrument_id: "885002.TI", kind: "concept", name: "缺报价", change_pct: null };
const comparison = Array.from({ length: 710 }, (_, i) => ({
  instrument_id: `${880000 + i}.TI`, name: `对照${i}`, kind: "industry",
  change_pct: i === 0 ? null : 710 - i, previous_change_pct: 0, state: "both_up",
}));
const value = {
  previous_date: "2026-10-29", current_leaders: [concept, unknown, industry],
  current_laggards: [{ ...concept, change_pct: -3 }, { ...industry, change_pct: -1 }], comparison,
};
const original = JSON.stringify(value);
let activeSlots, cursor;
const directionSlots = [], tableSlots = new Map(), chartCalls = [];
globalThis.__noonDirectionHooks = {
  useState(initial) {
    const slots = activeSlots, index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
    return [slots[index], (next) => { slots[index] = typeof next === "function" ? next(slots[index]) : next; }];
  },
  useRef() { return { current: null }; },
};
const output = await build({
  configFile: false, root: new URL("../../../", import.meta.url).pathname, logLevel: "silent",
  plugins: [{
    name: "noon-direction-interaction-hooks", enforce: "pre",
    resolveId(source, importer) {
      if (source === "react" && importer?.endsWith("/NoonWorkbenchAnalysis.tsx")) return "\0noon-direction-hooks";
    },
    load(id) {
      if (id === "\0noon-direction-hooks") return "export const {useState,useRef}=globalThis.__noonDirectionHooks;";
    },
  }],
  build: { write: false, ssr: new URL("./NoonWorkbenchAnalysis.tsx", import.meta.url).pathname, minify: false },
});
const require = createRequire(import.meta.url);
const entry = output.output.find((item) => item.type === "chunk" && item.isEntry);
const code = entry.code.replace(/from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`);
const { NoonDirections } = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
const nodes = (node) => !node || typeof node !== "object" ? [] : Array.isArray(node)
  ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null ? "" : typeof node !== "object" ? String(node)
  : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
let tree, tableTree, options = { value, snapshotId, onSector: (...args) => chartCalls.push(args) };
function render() {
  activeSlots = directionSlots; cursor = 0;
  tree = NoonDirections(options);
  const table = nodes(tree).find((node) => node.props?.onSelect);
  if (!tableSlots.has(table.key)) tableSlots.set(table.key, []);
  activeSlots = tableSlots.get(table.key); cursor = 0;
  tableTree = table.type(table.props);
}
const button = (label, root = tree) => nodes(root).find((node) => node.type === "button" &&
  (node.props["aria-label"] === label || text(node) === label));
const embedded = () => nodes(tree).find((node) => node.props?.embedded);
const bodyRows = () => nodes(nodes(tableTree).find((node) => node.type === "tbody")).filter((node) => node.type === "tr");
const click = (node) => { assert.ok(node); node.props.onClick({ stopPropagation() {} }); render(); };
try {
  render();
  assert.equal(embedded().props.topic.topic_code, industry.instrument_id, "highest valid quote is selected initially");
  assert.equal(embedded().props.snapshotId, snapshotId);
  const firstKey = embedded().key;
  click(button("查看合成同名成分股", tableTree));
  click(bodyRows()[1]);
  assert.equal(embedded().props.topic.topic_code, concept.instrument_id, "same-name different-code rows stay distinct");
  assert.notEqual(embedded().key, firstKey, "switching source remounts member filters and pagination");
  click(button("查看合成同名板块K线", tableTree));
  assert.equal(chartCalls[0][1], "chart");
  assert.equal(embedded().props.topic.topic_code, concept.instrument_id, "chart action does not change the selected member row");
  click(button("行业"));
  assert.equal(bodyRows().length, 1);
  assert.equal(embedded().props.topic.topic_code, industry.instrument_id);
  click(button("全部"));
  const search = nodes(tree).find((node) => node.props?.["aria-label"] === "搜索午间板块");
  search.props.onChange({ target: { value: "885001" } }); render();
  assert.equal(bodyRows().length, 1);
  assert.equal(embedded().props.topic.topic_code, concept.instrument_id);
  nodes(tree).find((node) => node.props?.["aria-label"] === "搜索午间板块").props.onChange({ target: { value: "不存在" } }); render();
  assert.equal(embedded(), undefined, "empty search clears members rather than retaining previous data");
  assert.match(text(tree), /当前范围没有匹配方向/);
  click(button("清除搜索"));
  click(button("本次偏弱方向2"));
  assert.equal(embedded().props.topic.topic_code, concept.instrument_id, "weak directions start with the lowest change");
  click(button("板块跨日对照710"));
  assert.equal(bodyRows().length, 12);
  assert.match(text(tableTree), /1 \/ 60/);
  assert.match(text(tableTree), /对照12/);
  click(button("板块跨日对照下一页", tableTree));
  assert.match(text(tableTree), /2 \/ 60/);
  assert.match(text(tableTree), /对照24/);
  const sortHeader = nodes(tableTree).find((node) => node.props?.column?.key === "change_pct");
  sortHeader.props.onSort("change_pct"); render();
  assert.match(text(tableTree), /1 \/ 60/, "sort returns to first page");
  assert.match(text(bodyRows()[0]), /对照709/);
  assert.doesNotMatch(text(tableTree), /对照0成分股/, "unknown quotes stay last even in ascending order");
  const previousKey = embedded().key;
  options = { ...options, snapshotId: "noon_" + "b".repeat(32) }; render();
  assert.notEqual(embedded().key, previousKey, "date/capture identity resets the embedded member state");
  options = { ...options, snapshotId: undefined }; render();
  assert.equal(embedded(), undefined, "missing snapshot cannot fall back to current members");
  assert.match(text(tree), /成分股暂不可用/);
  options = { ...options, snapshotId, value: { comparison: [{ ...industry, topic_code: concept.instrument_id }] } }; render();
  assert.equal(embedded(), undefined, "conflicting source codes cannot be read as a same-name replacement");
  assert.equal(JSON.stringify(value), original);
} finally {
  delete globalThis.__noonDirectionHooks;
}
console.log("noon directions: selection, chart actions, scoped filtering, 710-row pagination, capture reset and missing identity passed");
