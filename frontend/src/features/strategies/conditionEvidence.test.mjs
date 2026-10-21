import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./conditionEvidence.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { yearlyLimitCountText } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const field = "limit_up_count_1y";
assert.equal(yearlyLimitCountText({ field, actual: 0 }), "实际 0 次");
assert.equal(yearlyLimitCountText({ field, actual: 5 }), "实际 5 次");
assert.equal(
  yearlyLimitCountText({
    field,
    actual: null,
    actual_lower_bound: 3,
    actual_upper_bound: 20,
  }),
  "已确认至少 3 次，完整次数待核验",
);
assert.equal(
  yearlyLimitCountText({
    field,
    actual: null,
    actual_lower_bound: 0,
    actual_upper_bound: null,
  }),
  "已确认至少 0 次，完整次数待核验",
);
for (const actual of [-1, 1.5, NaN, Infinity, "3", false])
  assert.equal(yearlyLimitCountText({ field, actual }), "近一年次数待核验");
assert.equal(
  yearlyLimitCountText({
    field,
    actual: null,
    actual_lower_bound: 4,
    actual_upper_bound: 2,
  }),
  "近一年次数待核验",
);
assert.equal(yearlyLimitCountText({ field: "limit_up_ytd", actual: 3 }), null);
console.log(
  "yearly limit count: exact zero, integer count, incomplete bounds and invalid evidence presentation passed",
);
