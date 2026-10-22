import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./format.ts", import.meta.url),
  "utf8",
);
const start = source.indexOf("export function duration(");
const end = source.indexOf("export const tone", start);
const compiled = ts.transpileModule(source.slice(start, end), {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { duration } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
for (const value of [
  null,
  undefined,
  "",
  false,
  "276000",
  NaN,
  Infinity,
  -1,
  {},
])
  assert.equal(duration(value), "—");
for (const [value, expected] of [
  [0, "0秒"],
  [1, "不足1秒"],
  [999, "不足1秒"],
  [1000, "1秒"],
  [59000, "59秒"],
  [59999, "59秒"],
  [60000, "1分00秒"],
  [61000, "1分01秒"],
  [276000, "4分36秒"],
  [3599999, "59分59秒"],
  [3600000, "1小时00分00秒"],
  [3725000, "1小时02分05秒"],
  [93600000, "26小时00分00秒"],
])
  assert.equal(duration(value), expected);
console.log(
  "duration: invalid values, subsecond, minute/hour boundaries and long total wall-clock formatting passed",
);
