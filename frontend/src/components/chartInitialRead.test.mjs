import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
    },
  }).outputText;
const { candleChartEndpoint, initialChartResponse } = await import(
  encode(
    compile(
      await fs.readFile(new URL("./chartEndpoint.ts", import.meta.url), "utf8"),
    ),
  )
);
const endpoint = "/market/sector-chart?topic_code=881001.TI&date=2026-10-27";
const first = {
  instrument: { instrument_id: "881001.TI" },
  period: "D",
  as_of: "2026-10-27",
  snapshot_id: "market-one",
  price_basis: "none",
  available: true,
  bars: [{ date: "2026-10-27", close: 101 }],
};
const initial = { endpoint, data: first };
assert.equal(
  initialChartResponse(initial, endpoint, "881001.TI", "2026-10-27", "D"),
  first,
);
for (const changed of [
  { endpoint: endpoint + "&other=1" },
  { data: { ...first, as_of: "2026-10-26" } },
  { data: { ...first, instrument: { instrument_id: "881002.TI" } } },
  { data: { ...first, period: "W" } },
  { data: { ...first, price_basis: "forward_event_affine" } },
  { data: { ...first, snapshot_id: null } },
  { data: { ...first, available: false, bars: [] } },
])
  assert.equal(
    initialChartResponse(
      { ...initial, ...changed },
      endpoint,
      "881001.TI",
      "2026-10-27",
      "D",
    ),
    null,
  );
assert.equal(
  initialChartResponse(initial, endpoint, "881001.TI", "2026-10-27", "W"),
  null,
);

// Exercise the actual chart's initial-read lifecycle, without loading ECharts.
const source = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Charts.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const component = ast.statements.find(
  (node) => ts.isFunctionDeclaration(node) && node.name?.text === "CandleChart",
);
const statements = [...component.body.statements];
const start = statements.findIndex((node) =>
  node.getText(ast).startsWith("const [initialRequestPath]"),
);
const end = statements.findIndex(
  (node, i) => i > start && node.getText(ast).startsWith("const data ="),
);
assert.ok(start > 0 && end > start);
const code = compile(
  statements
    .slice(start, end + 1)
    .map((node) => node.getText(ast))
    .join("\n"),
);
function reader() {
  const state = [],
    effects = [],
    requests = [];
  let cursor = 0,
    pending = [],
    previous;
  const scope = {
    initialChart: initial,
    dataEndpoint: endpoint,
    instrumentId: "881001.TI",
    asOf: "2026-10-27",
    period: "D",
    frozenMode: false,
    frozenChartsByPeriod: {},
    chartRequestPath: "",
    initialChartResponse,
    useState(value) {
      const i = cursor++;
      if (!(i in state))
        state[i] = typeof value === "function" ? value() : value;
      return [
        state[i],
        (next) => {
          state[i] = next;
        },
      ];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!effects[i] || deps.some((value, n) => value !== effects[i][n]))
        pending.push(() => {
          effects[i] = deps;
          fn();
        });
    },
    useApi(path) {
      if (path !== previous) {
        if (path) requests.push(path);
        previous = path;
      }
      return { data: path ? { fresh: true, period: scope.period } : null };
    },
  };
  function render(changes = {}) {
    Object.assign(scope, changes);
    cursor = 0;
    scope.chartRequestPath = scope.frozenMode
      ? null
      : candleChartEndpoint({
          ...scope,
          priceBasis: "none",
          indicatorContinuity: "quotes",
        });
    const data = Function(
      ...Object.keys(scope),
      code + "\nreturn data;",
    )(...Object.values(scope));
    const work = pending;
    pending = [];
    work.forEach((fn) => fn());
    return data;
  }
  return { render, requests };
}
const view = reader();
assert.equal(view.render(), first);
assert.deepEqual(
  view.requests,
  [],
  "a verified first D response never starts a duplicate GET",
);
const refreshed = { ...first, bars: [{ date: first.as_of, close: 102 }] };
assert.equal(
  view.render({ initialChart: { endpoint, data: refreshed } }),
  refreshed,
  "the owner's explicit refresh updates the first D view",
);
assert.deepEqual(view.render({ period: "W" }), { fresh: true, period: "W" });
assert.deepEqual(view.render({ period: "D" }), { fresh: true, period: "D" });
assert.equal(
  view.requests.length,
  2,
  "W and return-to-D each read their own current payload",
);
assert.match(view.requests[0], /period=W/);
assert.match(view.requests[1], /period=D/);
const wrong = reader();
assert.equal(
  wrong.render({
    initialChart: { endpoint, data: { ...first, as_of: "2026-10-26" } },
  }).fresh,
  true,
);
assert.equal(
  wrong.requests.length,
  1,
  "wrong-day data cannot suppress the actual read",
);
const frozen = reader();
const frozenData = { immutable: true };
assert.equal(
  frozen.render({ frozenMode: true, frozenChartsByPeriod: { D: frozenData } }),
  frozenData,
);
assert.deepEqual(
  frozen.requests,
  [],
  "frozen research charts retain their independent no-fetch contract",
);
console.log(
  "sector initial D reuse: identity, unknowns, owner refresh, W→D fresh reads and frozen isolation passed",
);
