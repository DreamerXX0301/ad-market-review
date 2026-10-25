import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const compile = (value, fileName = "helper.ts") =>
  ts.transpileModule(value, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const source = await fs.readFile(
  new URL("./trackingPricePresentation.ts", import.meta.url),
  "utf8",
);
const { trackingPriceContext: tracking } = await import(
  encode(compile(source))
);
const { backtestPriceContext: backtest } = await import(
  encode(
    compile(
      await fs.readFile(
        new URL("../backtests/backtestPricePresentation.ts", import.meta.url),
        "utf8",
      ),
    ),
  )
);
const none = {
  entry_basis: { price_basis: "none" },
  latest_price: 12,
  entry_price: 10,
  tracking_change_pct: 20,
};
assert.equal(tracking(none).forward, false);
assert.equal(tracking(none).label, "跟踪涨幅");
assert.equal(tracking(none).change, 20);
const forward = {
  ...none,
  entry_basis: { signal_price_basis: "forward" },
  valuation_date: "2026-10-23",
  tracking_reference_price: 9.5,
  tracking_reference_price_basis: "forward_event_affine",
  tracking_comparison_anchor: "2026-10-23",
  tracking_reference_evidence: { state: "ready" },
  tracking_change_pct: 26.315789,
};
const before = structuredClone(forward);
assert.equal(tracking(forward).label, "同口径价格变化");
assert.equal(tracking(forward).reference, 9.5);
assert.equal(tracking(forward).anchor, "2026-10-23");
assert.equal(
  tracking(forward).change,
  forward.tracking_change_pct,
  "use the frozen server price change rather than computing from raw latest_price",
);
assert.match(tracking(forward).scope, /不含分红到账/);
for (const update of [
  { tracking_reference_price: null },
  { tracking_reference_price: 0 },
  { tracking_comparison_anchor: "2026-10-22" },
  { tracking_comparison_anchor: "2026-02-30" },
  { tracking_reference_evidence: { state: "unknown" } },
]) {
  const value = tracking({ ...forward, ...update });
  assert.equal(value.reference, null);
  assert.equal(value.change, null);
  assert.match(value.reason, /手动核对/);
}
assert.equal(
  tracking({ ...forward, comparison_reason: "本观察日尚未核对" }).reason,
  "本观察日尚未核对",
);
assert.equal(
  tracking({
    entry_basis: { signal_price_basis: "forward" },
    comparison_reason: "请手动核对",
    latest_price: 12,
  }).reference,
  null,
);
assert.deepEqual(forward, before);
assert.doesNotMatch(source, /latest_price\s*\/|entry_price\s*\/|Date\.now/);
for (const value of [null, {}, { signal_price_basis: "none" }])
  assert.equal(backtest(value), null);
const contract = {
  signal_price_basis: "forward",
  execution_price_basis: "none",
  price_basis_contract: "strategy-local-forward-v1",
};
assert.equal(backtest(contract).label, "前复权信号 · 未复权撮合");
assert.match(backtest(contract).reason, /strategy-local-forward-v1/);
assert.match(backtest(contract).reason, /不等于含分红收益/);
assert.match(
  backtest({ ...contract, execution_price_basis: "unknown" }).label,
  /撮合口径待核验/,
);
const backtestSource = await fs.readFile(
  new URL("../backtests/Backtests.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Backtests.tsx",
  backtestSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const declarations = ast.statements
  .filter(
    (node) =>
      ts.isFunctionDeclaration(node) &&
      ["warningTexts", "BoundaryDetails"].includes(node.name?.text),
  )
  .map((node) =>
    compile(node.getText(ast), "Boundary.tsx")
      .replace(/^import .+;\n/gm, "")
      .replace(/export \{\};?\n?/g, ""),
  )
  .join("\n");
const Boundary = new Function(
  "_jsx",
  "_jsxs",
  "backtestPriceContext",
  `${declarations};return BoundaryDetails;`,
)(jsx, jsxs, backtest);
const raw = renderToStaticMarkup(
  jsx(Boundary, { warnings: [], summary: "受限研究样本" }),
);
assert.ok(!raw.includes("前复权信号"));
const rendered = renderToStaticMarkup(
  jsx(Boundary, {
    warnings: [],
    summary: "受限研究样本",
    priceContext: contract,
  }),
);
assert.match(rendered, /前复权信号 · 未复权撮合/);
assert.match(rendered, /strategy-local-forward-v1/);
assert.match(backtestSource, /priceContext=\{availability\}/);
assert.match(backtestSource, /priceContext=\{r\}/);
assert.match(backtestSource, /backtestPriceContext\(x\)/);
console.log(
  "Tracking/backtest price context: raw compatibility, frozen same-anchor references, pending manual checks, no client return calculation and signal/execution source notes passed",
);
