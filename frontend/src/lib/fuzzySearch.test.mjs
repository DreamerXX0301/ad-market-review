import assert from "node:assert/strict";
import { makeFuzzyMatcher } from "./fuzzySearch.ts";

const stock = ["日出东方", "603366.SH", ["光伏设备", "家用电器"]];
for (const query of [
  "日出",
  "RCDF",
  "CDF",
  "ｒｃ",
  "ri chu",
  "6033",
  "０３３６６",
  "日出 gfsb",
]) {
  assert.ok(makeFuzzyMatcher(query)(...stock), query);
}
for (const query of ["603367", "日出 半导体", "gfzz"]) {
  assert.equal(makeFuzzyMatcher(query)(...stock), false, query);
}
assert.ok(makeFuzzyMatcher("   ")());
assert.ok(makeFuzzyMatcher("rgzn")("人工智能", null, undefined));
