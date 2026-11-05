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
export const buttons=[];function see(type,props){if(type==='button'||type==='select')buttons.push({...props,tag:type});}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`);
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
const { Stocks, discoveryThemeDistribution } = await import(await moduleUrl(new URL("./AuctionDiscovery.tsx", import.meta.url)));
const { reset } = await import(reactProbe), { buttons } = await import(jsxProbe);
const items = Array.from({length:27}, (_, index) => ({
  instrument_id:`${String(600000+index)}.SH`, name:`合成股票${String(index).padStart(2,'0')}`,
  auction_pct:27-index, auction_amount:index*1000, board:'main',
  themes:index===26?[]:[
    {name:index<12?'合成主要行业':`合成行业${index%5}`, kind:'industry'},
    {name:'合成共同概念',kind:'concept'},
    {name:`合成概念${index%6}`,kind:'concept'},
    {name:'合成共同概念',kind:'concept'},
  ],
}));
const distribution=discoveryThemeDistribution([...items,items[0]]);
assert.equal(distribution[0].total,27,'duplicate security cannot inflate distribution totals');
assert.equal(distribution[0].missing,1);
assert.equal(distribution[1].missing,1);
assert.equal(distribution[1].groups.find(row=>row.name==='合成共同概念').count,26,'same stock/concept is counted once');
const clicks=[];
function render(clear=false){reset(clear);buttons.length=0;return renderToStaticMarkup(createElement(Stocks,{items,label:'合成竞价名单',chartDate:'2026-11-05',showDistribution:true,onStock:id=>clicks.push(id)})).replaceAll(/<!--[^]*?-->/g,'');}
let html=render(true);
assert.match(html,/按全部 27 只股票统计，概念可重叠/);
assert.match(html,/其余\d+个行业/);
assert.match(html,/其余\d+个概念/);
assert.match(html,/另有 1 只归属待核验/);
assert.match(html,/共 27 只 · 1–10/);
assert.match(html,/1 \/ 3 页/);
assert.match(html,/合成股票00/);assert.doesNotMatch(html,/>合成股票10</);
buttons.find(button=>button.children==='下一页').onClick();
html=render();assert.match(html,/共 27 只 · 11–20/);assert.match(html,/合成股票10/);assert.doesNotMatch(html,/>合成股票00</);
buttons.find(button=>button.tag==='select').onChange({target:{value:'20'}});
html=render();assert.match(html,/1–20/);assert.match(html,/1 \/ 2 页/);
const nameOf=button=>Array.isArray(button.children)&&button.children[0]?.props?.children;
buttons.find(button=>button.className==='discovery-distribution-chip'&&nameOf(button)==='合成主要行业').onClick();
html=render();assert.match(html,/共 12 只 · 1–12/);assert.doesNotMatch(html,/>合成股票12</);
assert.match(html,/按全部 27 只股票统计/,'filtering rows does not change the distribution denominator');
buttons.find(button=>button.className==='discovery-distribution-chip'&&nameOf(button)==='合成主要行业').onClick();
html=render();assert.match(html,/共 27 只 · 1–20/,'clicking the same theme again cancels it');
assert.equal((html.match(/<col style="width:/g)||[]).length,11,'all sortable columns have stable widths');
console.log('Auction discovery: complete-list distribution, unique stock counts, missing affiliations, full sorting then paging, page-size reset and click-to-cancel passed');
