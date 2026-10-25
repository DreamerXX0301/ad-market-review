import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./chartPriceBasis.ts", import.meta.url),
  "utf8",
);
const { chartPriceBasis: view } = await import(
  "data:text/javascript;base64," +
    Buffer.from(
      ts.transpileModule(source, {
        compilerOptions: {
          module: ts.ModuleKind.ES2022,
          target: ts.ScriptTarget.ES2022,
        },
      }).outputText,
    ).toString("base64")
);
assert.equal(view({ price_basis: "none" }).label, "未复权");
assert.equal(view({ price_basis: "none" }, true).label, "指数原始点位");
const forward = {
  requested_price_basis: "forward",
  price_basis: "forward_event_affine",
  as_of: "2026-10-23",
};
assert.equal(view(forward).label, "本地前复权");
assert.match(view(forward).reason, /2026-10-23/);
assert.match(view(forward).reason, /顶部原始收盘报价/);
assert.equal(
  view({
    ...forward,
    price_basis: "none",
    adjustment: { reason: "公司行动不完整" },
  }).label,
  "未复权（前复权不可用）",
);
assert.equal(
  view({
    ...forward,
    price_basis: "none",
    adjustment: { reason: "公司行动不完整" },
  }).reason,
  "公司行动不完整",
);
for (const data of [
  null,
  {},
  { requested_price_basis: "forward" },
  { price_basis: "forward" },
  { price_basis: "unknown" },
])
  assert.equal(
    view(data).label,
    "价格口径待核验",
    "the selected preference cannot prove the returned price basis",
  );
const chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
assert.match(chartSource, /chartPriceBasis\(data, isIndex\)/);
assert.match(chartSource, /图表价格：\{actualPriceBasis.label\}/);
console.log(
  "Actual chart price basis: raw/forward/index, failed-forward fallback and missing response evidence passed",
);
