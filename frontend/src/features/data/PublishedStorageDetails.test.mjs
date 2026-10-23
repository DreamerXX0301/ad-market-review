import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const modules = new Map();
async function load(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let code = ts.transpileModule(await fs.readFile(path, "utf8"), { fileName: path.pathname, compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  for (const match of [...code.matchAll(/(?:from |import )(["'])([^"']+)\1/g)]) {
    const name = match[2];
    const target = name.startsWith(".") ? await load(new URL(name + ".ts", path)) : import.meta.resolve(name);
    code = code.replace(match[0], match[0].replace(match[1] + name + match[1], JSON.stringify(target)));
  }
  const url = "data:text/javascript;base64," + Buffer.from(code).toString("base64"); modules.set(path.href, url); return url;
}
const { publishedStorageView } = await import(await load(new URL("./storagePresentation.ts", import.meta.url)));
const { default: Details } = await import(await load(new URL("./PublishedStorageDetails.tsx", import.meta.url)));
const categories = ["current_market", "old_market", "supplement", "backtests", "metadata_other"].map((key, index) => ({ key, logical_bytes: index * 100_000_000, file_count: index }));
const accounting = { scope: "active_published_root", logical_bytes: 1_000_000_000, allocated_bytes: 1_100_000_000, complete: true, file_count: 10, categories };
function html(storage_accounting = accounting) { return renderToStaticMarkup(createElement(Details, { view: publishedStorageView({ storage_bytes: storage_accounting.logical_bytes, storage_accounting }) })); }
const rendered = html();
assert.match(rendered, /<details class="data-storage-details">/, "breakdown starts collapsed without an alarming banner");
for (const label of ["当前行情代际", "旧行情代际", "补充数据", "回测结果", "目录元数据与其他文件", "1.000 GB", "0.931 GiB", "1,000,000,000 B"]) assert(rendered.includes(label), label);
assert.match(rendered, /业务库、原始数据 raw、工作文件 work（含缓存、断点与核验记录）、其他运行版本和备份不在本项统计内/);
assert.match(rendered, /不代表磁盘独占占用或可释放空间/);
assert.match(rendered, /旧代际可能仍被历史事件、任务或报告引用/);
assert.doesNotMatch(rendered, /<button|清理全部|预计释放|缓存可删/);
const partial = html({ ...accounting, complete: false });
assert.match(partial, /已计量小计/); assert.match(partial, /不代表完整总量/);
const unavailable = renderToStaticMarkup(createElement(Details, { view: publishedStorageView({ storage_bytes: 1_000_000_000 }) }));
assert.doesNotMatch(unavailable, /<tbody>/); assert.match(unavailable, /尚未取得文件构成明细/);
console.log("published storage disclosure: real category totals, default collapse, scope exclusions, partial subtotal and no cleanup action passed");
