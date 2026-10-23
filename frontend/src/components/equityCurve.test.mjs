import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = ts.transpileModule(
  await fs.readFile(new URL("./equityCurve.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  equityCurvePoints,
  equityCurveTooltip,
  frozenInitialCapital,
  roundedInitialCapital,
  curveAmount,
  curvePercent,
} = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const reportA = Object.freeze({
  stats: Object.freeze({ initial_capital: 100, total_return: 10 }),
  config: Object.freeze({ initial_capital: 100 }),
});
const reportB = Object.freeze({
  stats: Object.freeze({ initial_capital: 200 }),
  config: Object.freeze({ initial_capital: 100 }),
});
const rows = Object.freeze([
  Object.freeze({ date: "2026-01-01", equity: 90, drawdown: 10 }),
  Object.freeze({ date: "2026-01-02", equity: 0, drawdown: 100 }),
  Object.freeze({ date: "2026-01-03", equity: 110, drawdown: 0 }),
]);
const points = equityCurvePoints(rows, frozenInitialCapital(reportA).value);
assert(
  Math.abs(points[0].cumulativeReturn + 10) < 1e-12,
  "first fee-bearing equity must not become the baseline",
);
assert.equal(points[1].cumulativeReturn, -100);
assert.equal(points[1].equity, 0);
assert(
  Math.abs(points[2].cumulativeReturn - reportA.stats.total_return) < 1e-12,
);
assert.equal(points[1].drawdown, -100);
assert(
  Math.abs(
    equityCurvePoints(rows, frozenInitialCapital(reportB).value)[0]
      .cumulativeReturn + 55,
  ) < 1e-12,
);
assert.equal(frozenInitialCapital(reportB).configMismatch, true);
assert.equal(
  frozenInitialCapital(reportA).value,
  100,
  "changing reports never changes another frozen denominator",
);
for (const value of [undefined, null, NaN, Infinity, -1, "100", true]) {
  const point = equityCurvePoints([{ equity: value, drawdown: value }], 100)[0];
  assert.equal(point.equity, null);
  assert.equal(point.cumulativeReturn, null);
  assert.equal(
    point.drawdown,
    typeof value === "number" &&
      Number.isFinite(value) &&
      Math.abs(value) <= 100
      ? -Math.abs(value)
      : null,
  );
  assert.equal(point.equityIssue, value == null ? "missing" : "invalid");
}
for (const value of [undefined, null, 0, -1, NaN, Infinity, "100", true]) {
  assert.equal(equityCurvePoints(rows, value)[0].cumulativeReturn, null);
  assert.equal(
    frozenInitialCapital({
      stats: { initial_capital: value },
      config: { initial_capital: 100 },
    }).value,
    null,
    "an invalid present stats field cannot be masked by config",
  );
}
for (const [input, expected] of [
  [1000.005, 1000.01],
  [1000.004, 1000],
  [1.005, 1.01],
  [0.005, 0.01],
  [0.004, null],
  [1e-7, null],
  [1e9 + 0.005, 1000000000.01],
  [1e10, 1e10],
])
  assert.equal(roundedInitialCapital(input), expected);
assert.deepEqual(
  frozenInitialCapital({ config: { initial_capital: 1000.005 } }),
  { value: 1000.01, source: "config", configMismatch: false },
);
assert.equal(
  frozenInitialCapital({
    stats: { initial_capital: 1000.01 },
    config: { initial_capital: 1000.005 },
  }).configMismatch,
  false,
);
assert.equal(frozenInitialCapital({}).source, null);
const actual = frozenInitialCapital({
  stats: { initial_capital: 1000.01 },
  config: { initial_capital: 1000.005 },
}).value;
assert.equal(
  equityCurvePoints(
    [
      { equity: 1000.01, drawdown: 0 },
      { equity: 968.23, drawdown: 3.178 },
    ],
    actual,
  )[0].cumulativeReturn,
  0,
  "explicit baseline row stays at zero",
);
assert.equal(curveAmount(0), "0.00 元");
assert.equal(curveAmount(96802.3), "96,802.30 元");
assert.equal(curvePercent(-0), "0.00%");
for (const mode of ["return", "equity"]) {
  const html = equityCurveTooltip(
    [{ dataIndex: 0 }, { dataIndex: 0 }],
    points,
    mode,
  );
  assert.equal(html.match(/2026-01-01/g).length, 1);
  assert.match(html, /-10.00%/);
  assert.match(html, /90.00 元/);
  assert.match(html, /累计收益率/);
  assert.match(html, /账户权益/);
  assert.match(html, /回撤/);
}
assert.match(
  equityCurveTooltip(
    { dataIndex: 0 },
    equityCurvePoints([{ date: '<img src="x">', equity: -1 }], 100),
    "return",
  ),
  /&lt;img src=&quot;x&quot;&gt;/,
);
assert.match(
  equityCurveTooltip(
    { dataIndex: 0 },
    equityCurvePoints([{ equity: -1 }], 100),
    "equity",
  ),
  /权益数值异常/,
);
assert.equal(equityCurveTooltip([{ dataIndex: 999 }], points, "return"), "");
console.log(
  "equity curve: frozen report capital, decimal cent rounding, valid zero, unknown gaps and auditable three-value tooltip passed",
);
