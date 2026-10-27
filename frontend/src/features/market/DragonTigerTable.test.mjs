import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const cache = new Map();
const encoded = value => "data:text/javascript;base64," + Buffer.from(value).toString("base64");
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let source = ts.transpileModule(await fs.readFile(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    source = source.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
    const paths = [...new Set([...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1]))];
    for (const name of paths) {
      let target;
      if (name.startsWith(".")) {
        const candidates = [new URL(name + ".ts", url), new URL(name + ".tsx", url)];
        for (const candidate of candidates) { try { await fs.access(candidate); target = await moduleUrl(candidate); break; } catch (error) { if (error.code !== "ENOENT") throw error; } }
        if (!target) throw new Error(`Missing source dependency ${name}`);
      } else target = import.meta.resolve(name);
      source = source.replaceAll('from "' + name + '"', 'from ' + JSON.stringify(target)).replaceAll("from '" + name + "'", 'from ' + JSON.stringify(target));
    }
    return encoded(source);
  })();
  cache.set(url.href, job); return job;
}
const { default: DragonTigerTable, dragonTigerRowControl } = await import(await moduleUrl(new URL("./DragonTigerTable.tsx", import.meta.url)));
const { dragonTigerRows, sortDragonTigerRows } = await import(await moduleUrl(new URL("./dragonTigerPresentation.ts", import.meta.url)));
const rows = sortDragonTigerRows(dragonTigerRows({ boards: { all: { items: Array.from({ length: 60 }, (_, index) => ({ instrument_id: `${600001 + index}.SH`, code: String(600001 + index), name: `合成${index}`, range_days: 1, change_pct: index, buy_value: index * 10000, sell_value: 0, net_value: index * 10000, org_net_value: null, source_concepts: [] })) } } }, "all"), { key: "net", order: "desc" }, "all");
const calls = [];
const props = { rows, board: "all", page: 1, sort: { key: "net", order: "desc" }, selectedKey: rows[0].key, seatOpen: true, onPage: value => calls.push(["page", value]), onSort: value => calls.push(["sort", value]), onSeat: row => calls.push(["seats", row.instrument_id]), onStock: row => calls.push(["chart", row.instrument_id]) };
const html = renderToStaticMarkup(createElement(DragonTigerTable, props));
assert.ok(html.includes('aria-sort="descending"'));
assert.ok(html.includes("合成59") && !html.includes(">合成34<"), "the first page follows the full sorted universe");
assert.ok(html.includes("复制六位代码 600060"), "stock codes use the shared standalone copy control");
assert.ok(html.includes("名称查看K线，整行查看席位"));
function walk(node, result = []) {
  if (!node || typeof node !== "object") return result;
  if (Array.isArray(node)) { for (const child of node) walk(child, result); return result; }
  if (node.props) { result.push(node); walk(node.props.children, result); }
  return result;
}
const tree = walk(DragonTigerTable(props));
const row = tree.find(node => node.type === "tr" && node.props.tabIndex === 0);
assert.equal(row.props["aria-expanded"], true);
row.props.onClickCapture({ target: { closest: () => null } });
assert.deepEqual(calls.pop(), ["seats", rows[0].instrument_id], "blank and numeric table cells activate seats");
row.props.onClickCapture({ target: { closest: () => ({ tagName: "BUTTON" }) } });
assert.equal(calls.length, 0, "nested buttons are excluded during row capture");
const name = walk(row).find(node => node.type === "button" && node.props.className === "stock-name-link");
let stopped = false;
name.props.onClick({ stopPropagation: () => { stopped = true; } });
assert.equal(stopped, true);
assert.deepEqual(calls.pop(), ["chart", rows[0].instrument_id], "the stock name opens the existing K-line callback, not seats");
row.props.onKeyDown({ target: row, currentTarget: row, key: "Enter", preventDefault() {} });
assert.deepEqual(calls.pop(), ["seats", rows[0].instrument_id]);
row.props.onKeyDown({ target: name, currentTarget: row, key: "Enter", preventDefault() { throw new Error("must not capture nested keyboard activation"); } });
assert.equal(calls.length, 0);
for (const target of [null, { closest: () => null }]) assert.equal(dragonTigerRowControl(target), false);
assert.equal(dragonTigerRowControl({ closest: () => ({}) }), true);
const sorts = tree.filter(node => node.type === "button" && node.props.className?.startsWith("dragon-tiger-sort"));
assert.equal(sorts.length, 6);
sorts[3].props.onClick(); assert.deepEqual(calls.pop(), ["sort", "buy"]);
const next = tree.find(node => node.type === "button" && node.props.children === "下一页");
next.props.onClick(); assert.deepEqual(calls.pop(), ["page", 2]);
const css = await fs.readFile(new URL("./DragonTiger.css", import.meta.url), "utf8");
assert.match(css, /\.dragon-tiger-table \.numeric\s*\{[^}]*text-align:\s*right/);
assert.match(css, /\.dragon-tiger-table \.numeric \.dragon-tiger-sort\s*\{[^}]*justify-content:\s*flex-end/);
console.log("dragon tiger table: numeric headers, complete-sort pagination, whole-row seats, name-only charts and independent code copy passed");
