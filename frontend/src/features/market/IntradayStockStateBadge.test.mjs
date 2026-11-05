import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const url = new URL("./IntradayStockStateBadge.tsx", import.meta.url);
const source = ts
  .transpileModule(await fs.readFile(url, "utf8"), {
    fileName: url.pathname,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "")
  .replaceAll(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  );
const { default: Badge, intradayStockStateBadges: states } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const labels = (row, broken) => states(row, broken).map((badge) => badge.label);

assert.deepEqual(labels({ change_pct: 20, previous_board_count: 2 }), []);
assert.deepEqual(
  labels({ state: "not_in_verified_pool", change_pct: 9.99 }),
  [],
);
assert.deepEqual(labels({ state: "unknown", board_count: 3 }), []);
assert.deepEqual(labels({ limit_up_state: "sealed", board_count: 1 }), [
  "首板",
]);
assert.deepEqual(labels({ limit_up_state: "sealed", continue_day_cnt: 4 }), [
  "4 连板",
]);
assert.deepEqual(labels({ state: "promoted", board_count: 2 }), ["2 连板"]);
assert.deepEqual(labels({ state: "sealed", board_count: null }), ["涨停"]);
assert.deepEqual(
  labels({ limit_up_state: "broken", state: "not_in_verified_pool" }),
  ["炸板"],
);
assert.deepEqual(labels({ state: "broken" }), ["炸板"]);
assert.deepEqual(labels({ state: "unknown" }, true), ["炸板"]);
assert.deepEqual(labels({ limit_up_state: "unknown" }, true), []);
assert.deepEqual(labels({ limit_down_state: "sealed" }), ["跌停"]);
assert.deepEqual(
  labels({ limit_up_state: "sealed", limit_down_state: "sealed" }),
  [],
);
assert.deepEqual(
  labels({
    limit_up_state: "sealed",
    board_count: 1,
    board_transition: "resealed",
  }),
  ["首板", "回封"],
);
assert.deepEqual(
  labels({ board_transition: "resealed", state: "unknown" }),
  [],
);

// The reported regression carries a separate current state from its cohort state.
const html = renderToStaticMarkup(
  createElement(Badge, {
    row: {
      instrument_id: "002150.SZ",
      state: "not_in_verified_pool",
      limit_up_state: "broken",
      change_pct: 7.696152,
    },
  }),
);
assert.match(html, /data-kind="broken"[^>]*>炸板<\/span>/);
assert.doesNotMatch(html, /首板|连板|涨停/);
assert.equal(renderToStaticMarkup(createElement(Badge, { row: {} })), "");
console.log("Intraday stock state badges passed");
