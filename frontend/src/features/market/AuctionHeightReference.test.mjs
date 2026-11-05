/** Actual component JSX with local API packets; no requests or collection tasks. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (text) => "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const react = import.meta.resolve("react"), runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe = encode(`export * from ${JSON.stringify(react)};
let cursor=0;const values=new Map();
export function reset(clear=false){cursor=0;if(clear)values.clear();}
export function useState(initial){const index=cursor++;if(!values.has(index))values.set(index,typeof initial==='function'?initial():initial);return[values.get(index),value=>values.set(index,typeof value==='function'?value(values.get(index)):value)];}`);
const apiProbe = encode(`let packet=null,loading=false,error='';export const requests=[];
export function setPacket(value,state={}){packet=value;loading=!!state.loading;error=state.error||'';requests.length=0;}
export function useApi(path){requests.push(path);return{data:packet,loading,error,reload(){}};}`);
const jsxProbe = encode(`import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};
export const buttons=[];function see(type,props){if(type==='button')buttons.push(props);}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`);
const replacements = { react: reactProbe, "react/jsx-runtime": jsxProbe, "../../lib/api": apiProbe };
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const pending = (async () => {
    let source = ts.transpileModule(await fs.readFile(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    source = source.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
    for (const name of [...new Set([...source.matchAll(/from\s+["']([^"']+)["']/g)].map((match) => match[1]))]) {
      let target = replacements[name];
      if (!target && name.startsWith(".")) {
        for (const suffix of [".ts", ".tsx"]) {
          const candidate = new URL(name + suffix, url);
          try { await fs.access(candidate); target = await moduleUrl(candidate); break; }
          catch (error) { if (error.code !== "ENOENT") throw error; }
        }
      }
      target ||= import.meta.resolve(name);
      source = source.replaceAll('from "' + name + '"', 'from ' + JSON.stringify(target)).replaceAll("from '" + name + "'", 'from ' + JSON.stringify(target));
    }
    return encode(source);
  })();
  cache.set(url.href, pending);
  return pending;
}
const { default: HeightReference } = await import(await moduleUrl(new URL("./AuctionHeightReference.tsx", import.meta.url)));
const { reset } = await import(reactProbe), { buttons } = await import(jsxProbe), { setPacket, requests } = await import(apiProbe);
const date = "2026-11-05", previous = "2026-11-04";
const theme = (name, code, kind = "concept") => ({ topic_code: code, name, kind, members_observed_date: previous });
const stock = { instrument_id: "000001.SZ", name: "合成超长名称用于排版边界验证股份", auction_pct: 9.99, opening_reference_price: 11.23,
  themes: [theme("合成行业", "881001.TI", "industry"), theme("深股通", "886001.TI"), theme("合成主概念", "886002.TI"), theme("合成次概念", "886003.TI"), theme("合成其余概念", "886004.TI"), theme("合成另一行业", "881002.TI", "industry")] };
const packet = { date, previous_date: previous, highest_board: 3, candidate_count: 100, verified_height_count: 90, unknown_height_count: 2, reason: null,
  groups: [{ board_count: 3, up: 1, down: 0, flat: 0, unknown: 1, items: [stock, { instrument_id: "000002.SZ", name: "合成待核验股", auction_pct: null, themes_reason: "本次成员名单缺失" }] },
    { board_count: 2, up: 0, down: 1, flat: 1, unknown: 0, items: [{ instrument_id: "000003.SZ", name: "合成下跌股", auction_pct: -1.23, themes: [theme("合成单概念", "886009.TI")] }, { instrument_id: "000004.SZ", name: "合成平开股", auction_pct: 0, themes: [] }] }] };
const clicks = [];
function render({ clear = false, clickable = true } = {}) {
  reset(clear); buttons.length = 0;
  return renderToStaticMarkup(createElement(HeightReference, { date, onStock: clickable ? (id) => clicks.push(id) : undefined })).replaceAll(/<!--[^]*?-->/g, "");
}
setPacket(packet);
let html = render({ clear: true });
assert.deepEqual(requests, [`/market/intraday/auction-reference?date=${date}`]);
assert.match(html, /class="ahr-header"/);
assert.match(html, /前收 <time>2026-11-04<\/time>/);
assert.match(html, /候选最高 <b>3<\/b> 板/);
assert.match(html, /已核验 <b>90 \/ 100<\/b> 只/);
assert.match(html, /板数待核验 2 只/);
assert.equal((html.match(/class="ahr-group" open=""/g) || []).length, 2, "both groups keep their stock lists expanded");
assert.match(html, /<details class="ahr-method"><summary>口径说明<\/summary>/, "long methodology stays in a closed disclosure");
assert.match(html, /最高板数仅限本份竞价候选/);
assert.match(html, /封板状态待盘中核验/);
assert.match(html, /合成行业/); assert.match(html, /合成主概念/); assert.match(html, /合成次概念/);
assert.doesNotMatch(html, />深股通<|>合成其余概念<|>合成另一行业</, "only one industry and two meaningful concepts start visible");
assert.match(html, /aria-expanded="false"[^>]*>\+3 更多<\/button>/);
assert.match(html, /名单留存日 2026-11-04/);
assert.match(html, /触及涨停参考价 11\.23 元/);
assert.match(html, /本次成员名单缺失/); assert.match(html, /报价待核验/);
assert.match(html, /-1\.23%/); assert.match(html, /0\.00%/);
assert.doesNotMatch(html, /NaN|undefined/);
const heads = [...html.matchAll(/class="ahr-stock-head"[^]*?<\/div>/g)].map((match) => match[0]);
assert.equal(heads.length, 4);
assert.match(heads[0], /合成超长名称用于排版边界验证股份/);
assert.match(heads[0], /复制六位代码 000001/);
assert.match(heads[0], /\+9\.99%/);
assert.doesNotMatch(heads[0], /data-kind|合成行业|涨停参考价/, "tag and reference text cannot enter the identity/quote grid");
buttons.find((button) => button.className === "ahr-stock-name").onClick();
assert.deepEqual(clicks, ["000001.SZ"]);
buttons.find((button) => button.className === "ahr-themes-more").onClick();
html = render();
for (const name of ["深股通", "合成其余概念", "合成另一行业"]) assert.ok(html.includes(`>${name}</span>`));
assert.match(html, /aria-expanded="true"[^>]*>收起<\/button>/);
buttons.find((button) => button.className === "ahr-themes-more").onClick();
html = render();
assert.doesNotMatch(html, />深股通<|>合成其余概念<|>合成另一行业</);
html = render({ clear: true, clickable: false });
assert.match(html, /<span class="ahr-stock-name">合成超长名称用于排版边界验证股份<\/span>/);
assert.match(html, /复制六位代码 000001/, "read-only stock rows retain code copying");
setPacket({ ...packet, date: "2026-11-04" });
html = render({ clear: true });
assert.doesNotMatch(html, /合成超长名称|90 \/ 100|合成主概念/, "another date cannot lend quotes or membership");
setPacket({ ...packet, groups: [], reason: "无可核验来源" });
html = render({ clear: true });
assert.match(html, /暂无可核验的前日高度板/); assert.match(html, /无可核验来源/);
const css = await fs.readFile(new URL("./AuctionHeightReference.css", import.meta.url), "utf8");
assert.match(css, /\.ahr-stock-head\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto 6em/);
assert.match(css, /@container auction-height \(max-width: 760px\)[^]*?\.ahr-groups\s*\{\s*grid-template-columns: minmax\(0, 1fr\)/);
assert.match(css, /overflow-wrap:\s*anywhere/);
assert.match(css, /\.ahr-stock-change\s*\{[^}]*text-align:\s*right/);
console.log("Auction height reference: compact default themes, expand/collapse, preserved quotes and unknowns, fixed identity columns, responsive groups and dated-source guards passed");
