import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import { performance } from "node:perf_hooks";
import { createElement, Children } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const load = async (path) =>
  import(
    encode(compile(await fs.readFile(new URL(path, import.meta.url), "utf8")))
  );
const { availableSortLevels, moveSortLevel, sortTableRowsByLevels } =
  await load("./multiTableSort.ts");
const { trackingProgressSortValue } = await load(
  "../features/tracking/trackingFilterLogic.ts",
);
const { trackingPriceContext } = await load(
  "../features/tracking/trackingPricePresentation.ts",
);
const descending = [
  { key: "trading_age", order: "desc" },
  { key: "today_change_pct", order: "desc" },
];
const value = (row, key) => row[key],
  identity = (row) => `${row.instrument_id || ""}/${row.round_id}`;
const sort = (rows, levels = descending) =>
  sortTableRowsByLevels(rows, levels, value, identity);
const ids = (rows) => rows.map((row) => row.round_id);

test("multiple raw numeric priorities precede pagination and preserve the source", () => {
  const rows = [
    { round_id: "a", trading_age: 4, today_change_pct: 9 },
    { round_id: "b", trading_age: 8, today_change_pct: 2.001 },
    { round_id: "c", trading_age: 8, today_change_pct: 2.009 },
    { round_id: "d", trading_age: 9, today_change_pct: -5 },
  ];
  const before = structuredClone(rows);
  assert.deepEqual(ids(sort(rows)), ["d", "c", "b", "a"]);
  assert.deepEqual(
    ids(sort(rows).slice(0, 2)),
    ["d", "c"],
    "a later input page can lead the full sorted list",
  );
  assert.deepEqual(rows, before);
  assert.deepEqual(
    ids(sort(rows, [{ key: "trading_age", order: "asc" }, descending[1]])),
    ["a", "c", "b", "d"],
  );
  assert.deepEqual(
    ids(sort(rows, [descending[0], { key: "today_change_pct", order: "asc" }])),
    ["d", "b", "c", "a"],
  );
});

test("unknown, invalid numeric and empty values stay last at their priority in both directions", () => {
  const rows = [null, undefined, NaN, Infinity, "", 0, -2, 2].map(
    (trading_age, i) => ({ round_id: String(i), trading_age }),
  );
  for (const order of ["asc", "desc"]) {
    const result = sort(rows, [{ key: "trading_age", order }]);
    assert.deepEqual(
      result.slice(0, 3).map((row) => row.trading_age),
      order === "asc" ? [-2, 0, 2] : [2, 0, -2],
    );
    assert.deepEqual(ids(result.slice(3)), ["0", "1", "2", "3", "4"]);
  }
  assert.deepEqual(
    ids(
      sort([
        {
          round_id: "missing-secondary",
          trading_age: 8,
          today_change_pct: null,
        },
        { round_id: "known-secondary", trading_age: 8, today_change_pct: -3 },
        { round_id: "lower-primary", trading_age: 7, today_change_pct: 10 },
      ]),
    ),
    ["known-secondary", "missing-secondary", "lower-primary"],
  );
});

test("identity tie break is deterministic across response order, with duplicate identities stable", () => {
  const a = {
      instrument_id: "600002.SH",
      round_id: "2",
      trading_age: 5,
      today_change_pct: 1,
    },
    b = { ...a, instrument_id: "600001.SH", round_id: "3" },
    c = { ...b, round_id: "2" };
  assert.deepEqual(ids(sort([a, b, c])), ["2", "3", "2"]);
  assert.deepEqual(sort([c, a, b]), sort([a, b, c]));
  const duplicate = { ...c, marker: "second" };
  assert.deepEqual(
    sort([c, duplicate]).map((row) => row.marker),
    [undefined, "second"],
  );
});

test("priority edits, removal, availability and reset leave defaults untouched", () => {
  const original = structuredClone(descending);
  const moved = moveSortLevel(descending, 1, -1);
  assert.deepEqual(moved, [descending[1], descending[0]]);
  assert.equal(moveSortLevel(descending, 0, -1), descending);
  assert.equal(moveSortLevel(descending, 1, 1), descending);
  assert.deepEqual(
    availableSortLevels(
      [...descending, descending[0], { key: "actions", order: "asc" }],
      ["trading_age", "today_change_pct"],
    ),
    descending,
  );
  assert.deepEqual(availableSortLevels(descending, ["today_change_pct"]), [
    descending[1],
  ]);
  const rows = [{ round_id: "1" }, { round_id: "2" }];
  assert.equal(
    sort(rows, []),
    rows,
    "removing every level restores input order",
  );
  assert.deepEqual(descending, original);
});

test("tracking sort uses displayed verified sessions and comparable price changes", () => {
  for (const missing of [null, undefined, NaN, -1, 0.5, "5"]) {
    assert.equal(
      trackingProgressSortValue({
        trading_age: missing,
        remaining_sessions: 2,
      }),
      null,
    );
    assert.equal(
      trackingProgressSortValue({
        trading_age: 5,
        remaining_sessions: missing,
      }),
      null,
    );
  }
  assert.equal(
    trackingProgressSortValue({ trading_age: 0, remaining_sessions: 30 }),
    0,
  );
  assert.equal(
    trackingProgressSortValue({ trading_age: 50, remaining_sessions: 0 }),
    50,
  );
  const rows = [
    {
      round_id: "longer",
      trading_age: 20,
      remaining_sessions: 40,
      policy: { duration_sessions: 60 },
    },
    {
      round_id: "shorter",
      trading_age: 10,
      remaining_sessions: 0,
      policy: { duration_sessions: 10 },
    },
  ];
  assert.deepEqual(
    ids(
      sortTableRowsByLevels(
        rows,
        [descending[0]],
        (row) => trackingProgressSortValue(row),
        identity,
      ),
    ),
    ["longer", "shorter"],
    "session count, not rounded progress-bar percentage",
  );
  const unknownForward = {
    round_id: "unknown",
    entry_basis: { signal_price_basis: "forward" },
    tracking_change_pct: 999,
  };
  assert.equal(trackingPriceContext(unknownForward).change, null);
  assert.deepEqual(
    ids(
      sortTableRowsByLevels(
        [unknownForward, { round_id: "known", tracking_change_pct: -2 }],
        [{ key: "tracking_change_pct", order: "desc" }],
        (row) => trackingPriceContext(row).change,
        identity,
      ),
    ),
    ["known", "unknown"],
  );
});

const controlsSource = await fs.readFile(
  new URL("./MultiSortControls.tsx", import.meta.url),
  "utf8",
);
const controlsCompiled = compile(controlsSource)
  .replace(/import "\.\/MultiSortControls\.css";/, "")
  .replace(
    /from "\.\/multiTableSort"/,
    `from "${encode(compile(await fs.readFile(new URL("./multiTableSort.ts", import.meta.url), "utf8")))}"`,
  )
  .replace(
    /from "(react\/jsx-runtime|lucide-react)"/g,
    (_, name) => `from "${import.meta.resolve(name)}"`,
  );
const { default: Controls } = await import(encode(controlsCompiled));
const choices = [
  { key: "trading_age", label: "进度" },
  { key: "today_change_pct", label: "今日涨幅" },
  { key: "name", label: "名称" },
];
const children = (node) => Children.toArray(node.props.children);
const find = (node, predicate) => {
  if (!node || typeof node !== "object") return undefined;
  if (predicate(node)) return node;
  for (const child of children(node)) {
    const found = find(child, predicate);
    if (found) return found;
  }
};
test("visible controls expose priorities and support real field/direction/move/delete/add/reset callbacks", () => {
  let changed = null,
    reset = 0;
  const props = {
    levels: descending,
    choices,
    onChange: (levels) => (changed = levels),
    onReset: () => reset++,
  };
  const tree = Controls(props);
  find(
    tree,
    (node) => node.props["aria-label"] === "第1排序字段",
  ).props.onChange({ target: { value: "name" } });
  assert.equal(changed[0].key, "name");
  find(
    tree,
    (node) => node.props["aria-label"] === "进度切换为升序",
  ).props.onClick();
  assert.equal(changed[0].order, "asc");
  find(
    tree,
    (node) => node.props["aria-label"] === "提高今日涨幅优先级",
  ).props.onClick();
  assert.deepEqual(changed, [descending[1], descending[0]]);
  find(
    tree,
    (node) => node.props["aria-label"] === "删除进度排序",
  ).props.onClick();
  assert.deepEqual(changed, [descending[1]]);
  find(
    tree,
    (node) => node.type === "button" && children(node).includes("添加排序"),
  ).props.onClick();
  assert.deepEqual(changed.at(-1), { key: "name", order: "desc" });
  find(
    tree,
    (node) => node.type === "button" && children(node).includes("重置排序"),
  ).props.onClick();
  assert.equal(reset, 1);
  assert.equal(
    find(tree, (node) => node.props["aria-label"] === "提高进度优先级").props
      .disabled,
    true,
  );
  const html = renderToStaticMarkup(createElement(Controls, props));
  assert.match(html, /多条件排序/);
  assert.match(html, /优先级1/);
  assert.match(html, /优先级2/);
  assert.match(html, /全部已加载记录先排序再分页/);
  assert.match(html, /value="today_change_pct" disabled=""/);
});

test("tracking and sector views opt in while generic Table keeps legacy single sorting", async () => {
  const table = await fs.readFile(new URL("./UI.tsx", import.meta.url), "utf8");
  assert.match(table, /multiSort = false/);
  assert.match(table, /sortTableRowsByLevels\(\s*rows,\s*activeLevels/);
  assert.match(table, /if \(multiSort\) setSortLevels\(\[next\]\)/);
  assert.ok(
    table.indexOf("sortTableRowsByLevels(") < table.indexOf("sorted.slice("),
  );
  assert.match(table, /onStockNavigate\(r.instrument_id, sorted, r\)/);
  const tracking = await fs.readFile(
    new URL("../features/tracking/Tracking.tsx", import.meta.url),
    "utf8",
  );
  const sector = await fs.readFile(
    new URL("../features/sector/SectorTracking.tsx", import.meta.url),
    "utf8",
  );
  assert.match(tracking, /multiSort[\s\S]*key: "trading_age"/);
  assert.match(tracking, /sortValue: trackingProgressSortValue/);
  assert.match(
    tracking,
    /sortValue: \(row\) => trackingPriceContext\(row\).change/,
  );
  assert.match(sector, /multiSort/);
  assert.match(sector, /sortValue: trackingProgressSortValue/);
  assert.match(tracking, /const rows = hotFilter.rows/);
});

test("5,000 synthetic rows sort all records with bounded value extraction; client-only timing", () => {
  const rows = Array.from({ length: 5000 }, (_, index) => ({
    round_id: String(index).padStart(5, "0"),
    instrument_id: String(5000 - index),
    trading_age: index % 61,
    today_change_pct: ((index * 17) % 1999) / 100 - 10,
  }));
  let extracts = 0;
  const started = performance.now();
  const sorted = sortTableRowsByLevels(
    rows,
    descending,
    (row, key) => {
      extracts++;
      return row[key];
    },
    identity,
  );
  const elapsed = performance.now() - started;
  assert.equal(
    extracts,
    10000,
    "values extracted once per row and priority, not on every comparison",
  );
  assert.equal(sorted.length, 5000);
  assert.equal(new Set(sorted.map((row) => row.round_id)).size, 5000);
  for (let index = 1; index < sorted.length; index++) {
    const previous = sorted[index - 1],
      current = sorted[index];
    assert.ok(
      previous.trading_age > current.trading_age ||
        (previous.trading_age === current.trading_age &&
          previous.today_change_pct >= current.today_change_pct),
    );
  }
  assert.deepEqual(sort([...rows].reverse()), sorted);
  console.log(
    JSON.stringify({
      scope: "pure client multi-sort; synthetic data, not API/strategy latency",
      rows: rows.length,
      priorities: 2,
      valueExtractions: extracts,
      elapsed_ms: +elapsed.toFixed(3),
    }),
  );
});
