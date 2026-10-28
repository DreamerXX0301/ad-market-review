import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./noonCopyPresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { noonLadderCopy } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const identityRows = [
  { instrument_id: "000678.SZ", name: "合成名称", change_pct: 10, limit_up_reason: "合成原因" },
  { instrument_id: "600001.SH" },
  { instrument_id: "830001.BJ" },
  { instrument_id: "000678.SZ" },
];
assert.equal(noonLadderCopy(identityRows), "000678\n600001\n830001", "copy keeps leading zeroes, original order and unique six-digit codes only");
const invalid = [null, undefined, false, 600001, "600001", "600001.XX", "600001.sh", "60001.SH", "6000001.SH", "600001.SH\n", " 600001.SH", "600001.SH\t"];
assert.equal(noonLadderCopy(invalid.map(instrument_id => ({instrument_id, name: "000678", code: "000678"}))), "", "invalid identities and code-looking names never produce copied codes");
assert.equal(noonLadderCopy([]), "");
for (const count of [71,79]) {
  const rows = Array.from({length:count}, (_,i) => ({instrument_id:`${String(i).padStart(6,"0")}.SH`, name:`合成样本${i}`, continue_day_cnt:1,
    change_pct:10, industries:[{name:"合成行业"}], topics:[{name:"合成概念"}], limit_up_reason:"合成原因\n多行\t内容"}));
  const before = JSON.stringify(rows), text = noonLadderCopy(rows), codes = text.split("\n");
  assert.equal(codes.length,count,"copy includes every retained code beyond the visible scroll area and detail page");
  assert.equal(codes.at(-1),String(count-1).padStart(6,"0"));
  assert.ok(codes.every(code => /^\d{6}$/.test(code)),"clipboard contains one six-digit code per line, without headers, markets, names or annotations");
  assert.equal(JSON.stringify(rows),before,"copy formatting never changes retained evidence");
}
console.log("noon copy: six-digit codes only, leading zeroes, stable deduplication, invalid identity filtering and complete retained rows passed");
