import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./sentimentPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  sentimentMetricView: metric,
  sentimentCount,
  sentimentCoverage,
  sentimentReferences,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

for (const key of [
  "equal_weight_return_pct",
  "median_return_pct",
  "previous_limit_up_avg_return_pct",
  "previous_limit_up_median_return_pct",
]) {
  assert.equal(metric({ key, value: 1.234, unit: "%" }).valueText, "+1.23");
  assert.equal(metric({ key, value: 1.234, unit: "%" }).tone, "up");
  assert.equal(metric({ key, value: -2.51, unit: "%" }).valueText, "-2.51");
  assert.equal(metric({ key, value: -2.51, unit: "%" }).tone, "down");
  assert.equal(metric({ key, value: 0, unit: "%" }).tone, "");
  assert.equal(metric({ key, value: 0, unit: "%" }).valueText, "0.00");
}
for (const key of [
  "up_ratio_pct",
  "promotion_rate_pct",
  "up_5pct_count",
  "down_5pct_count",
  "promoted_count",
  "failed_count",
  "highest_board_count",
]) {
  const unit = key.endsWith("_pct")
    ? "%"
    : key === "highest_board_count"
      ? "板"
      : "家";
  const view = metric({ key, value: 10, unit });
  assert.equal(
    view.tone,
    "",
    "positive ratios/counts must never masquerade as rising stock returns",
  );
  assert.equal(view.valueText.startsWith("+"), false);
}
for (const value of [null, undefined, NaN, Infinity, true, "10"]) {
  const view = metric({
    key: "promotion_rate_pct",
    value,
    unit: "%",
    formula: "晋级数 / 昨日涨停样本",
    reason: "昨日来源池未完整，晋级率未知",
  });
  assert.equal(view.known, false);
  assert.equal(view.valueText, "—");
  assert.equal(view.tone, "");
  assert.match(view.help, /昨日来源池未完整/);
}
for (const [unit, value] of [
  ["家", -1],
  ["板", 1.5],
  ["%", 101],
  ["%", -1],
]) {
  assert.equal(metric({ key: "up_ratio_pct", unit, value }).known, false);
}
assert.match(
  metric({ key: "median_return_pct", value: null, unit: "%" }).help,
  /不能按零/,
);
const partial = metric({
  key: "promoted_count",
  value: 2,
  unit: "家",
  reason: "已确认下界，仍有状态未知样本",
});
assert.equal(partial.valueText, "2");
assert.equal(partial.reason, "已确认下界，仍有状态未知样本");
assert.match(partial.help, /下界/);
assert.equal(sentimentCount(0), "0");
assert.equal(sentimentCount(5000), "5,000");
for (const value of [null, undefined, true, -1, 1.5, NaN])
  assert.equal(sentimentCount(value), "待核验");
assert.equal(sentimentCoverage(80), "80.0%");
assert.equal(sentimentCoverage(0), "0.0%");
for (const value of [null, -1, 101, true, Infinity])
  assert.equal(sentimentCoverage(value), "待核验");
assert.deepEqual(
  sentimentReferences([
    { url: "https://official.example/report", title: "来源" },
    { url: "https://official.example/report", title: "重复" },
    { url: "javascript:alert(1)" },
    null,
    {},
  ]).map((row) => row.title),
  ["来源"],
);
assert.deepEqual(sentimentReferences(null), []);
console.log(
  "sentiment presentation: exact return color whitelist, neutral ratios/counts, unknown-not-zero, partial proofs and safe source links passed",
);
