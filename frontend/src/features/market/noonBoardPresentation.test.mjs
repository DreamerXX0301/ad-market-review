import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const source = await fs.readFile(new URL("./noonBoardPresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { boardOf, filterRows, noonBoardOf, noonFilterBoards, boardLabel, NOON_BOARD_OPTIONS } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
// The verified stock-code families match domain/universe.py.identity_flags.
for (const [board, symbols] of Object.entries({
  main:["600001.SH","601001.SH","603001.SH","605001.SH","000001.SZ","001001.SZ","002001.SZ","003001.SZ"],
  chinext:["300001.SZ","301001.SZ"],star:["688001.SH","689001.SH"],
  beijing:["430001.BJ","830001.BJ","920001.BJ"],
})) for (const symbol of symbols) assert.equal(boardOf(symbol),board,symbol);
assert.equal(boardOf({code:"123456",exchange:"BSE"}),"beijing");
assert.equal(boardOf({instrument_id:"300001.SZ",board:"unknown"}),"unknown","explicit unknown identity is not silently reconstructed");
assert.equal(boardOf({instrument_id:"600001.SH",kind:"index"}),"unknown","a non-stock identity cannot gain a stock-board badge");
assert.equal(boardOf({instrument_id:"600001.SH",asset_type:"fund"}),"unknown");
assert.equal(boardOf({instrument_id:"600001.SH",board:"star"}),"star","an explicit validated server board is reused");
assert.equal(boardOf({thscode:"301001.SZ"}),"chinext");
for (const value of [null,undefined,"",{}, {name:"创业板"}, {board_label:"主板"}, "999999.UNKNOWN", "999999.SZ", "not-a-stock", {instrument_id:"invalid",name:"主板"}, {board:"not_a_board",instrument_id:"600001.SH"}]) assert.equal(boardOf(value),"unknown");
const rows = Object.freeze([
  Object.freeze({instrument_id:"600001.SH",change_pct:10}),
  Object.freeze({instrument_id:"300001.SZ",change_pct:-5}),
  Object.freeze({instrument_id:"688001.SH",change_pct:0}),
  Object.freeze({instrument_id:"920001.BJ",change_pct:3}),
  Object.freeze({instrument_id:"999999.UNKNOWN",change_pct:1}),
]);
const original = JSON.stringify(rows);
assert.notEqual(filterRows(rows,"all"),rows,"all still returns a separate view array");
assert.equal(filterRows(rows,"all").length,5,"unknown remains in the unfiltered list");
for (const [value,symbol] of [["main","600001.SH"],["chinext","300001.SZ"],["star","688001.SH"],["beijing","920001.BJ"]]) assert.deepEqual(filterRows(rows,value).map(row=>row.instrument_id),[symbol]);
assert.equal(noonBoardOf,boardOf); assert.equal(noonFilterBoards,filterRows);
assert.equal(JSON.stringify(rows),original,"display filtering never changes frozen rows or their values");
assert.equal(boardLabel("unknown"),"板别待核验");
assert.deepEqual(NOON_BOARD_OPTIONS.map(item=>item.value),["all","main","chinext","star","beijing"]);
const require = createRequire(import.meta.url);
const result = await build({configFile:false,root:new URL("../../../",import.meta.url).pathname,logLevel:"silent",build:{write:false,ssr:new URL("./NoonBoardFilter.tsx",import.meta.url).pathname,minify:false}});
const entry=result.output.find(item=>item.type==="chunk"&&item.isEntry);
const code=entry.code.replace(/from "([^".][^"]*)"/g,(_,id)=>`from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`);
const {default:Filter,NoonBoardTag}=await import("data:text/javascript;base64,"+Buffer.from(code).toString("base64"));
const markup=renderToStaticMarkup(createElement(Filter,{value:"star",onChange(){throw Error("render cannot update selection");}}));
assert.equal((markup.match(/aria-pressed="true"/g)||[]).length,1);
assert.match(markup,/<button[^>]*aria-pressed="true"[^>]*>科创板<\/button>/);
assert.ok(markup.includes("仅筛选显示")&&markup.includes("统计与市场温度不变"));
assert.equal(renderToStaticMarkup(createElement(NoonBoardTag,{row:{name:"创业板"}})),"");
assert.equal(renderToStaticMarkup(createElement(NoonBoardTag,{row:{board:"unknown",instrument_id:"688001.SH"}})),"");
const tag=renderToStaticMarkup(createElement(NoonBoardTag,{instrumentId:"301001.SZ"}));
assert.ok(tag.includes("创业板")&&tag.includes("noon-board-tag-chinext")&&tag.includes("不证明历史"));
console.log("noon boards: shared stock-code families, explicit unknown/non-stock guard, immutable display filters and controlled labels passed");
