import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
const encode=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
async function component(file) {
 let js=ts.transpileModule(await fs.readFile(new URL(file,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 js=js.replaceAll('"react/jsx-runtime"',JSON.stringify(import.meta.resolve('react/jsx-runtime'))).replaceAll('"../lib/format"',JSON.stringify(encode('export const duration=String;')));
 return (await import(encode(js))).default;
}
const Summary=await component('./WorkspaceTaskSummary.tsx'), Estimate=await component('./CollectionEstimate.tsx');
const html=renderToStaticMarkup(createElement(Summary,{workspace:{state:'completed',message:'已结束',completed_strategies:1,strategies:[{strategy_id:'a',name:'合成策略',matched:0,unknown:4,added_count:0}],tracking:{reviewed:0,skipped:2}}}));
assert.match(html,/合成策略/);assert.match(html,/命中 0，待核验 4，新入池 0/);assert.match(html,/跟踪核对 0 条/);
for(const upper of [null,NaN,Infinity,-1,0]) assert.match(renderToStaticMarkup(createElement(Estimate,{estimate:{remaining_min_ms:0,remaining_max_ms:upper}})),/暂无法估计/);
assert.match(renderToStaticMarkup(createElement(Estimate,{estimate:{remaining_min_ms:1000,remaining_max_ms:2000,sample_count:3}})),/预计还需 1000～2000/);
console.log('Collection summary preserves known zero/unknown results and refuses invented ETA');
