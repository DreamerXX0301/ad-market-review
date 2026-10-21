import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encoded = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = (text, fileName) =>
  ts.transpileModule(text, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const helpers = encoded(
  compile(
    await fs.readFile(
      new URL("./strategyResultPresentation.ts", import.meta.url),
      "utf8",
    ),
    "strategyResultPresentation.ts",
  ),
);
let source = compile(
  await fs.readFile(
    new URL("./StrategyResultTabs.tsx", import.meta.url),
    "utf8",
  ),
  "StrategyResultTabs.tsx",
);
source = source
  .replace(
    'from "./strategyResultPresentation"',
    `from ${JSON.stringify(helpers)}`,
  )
  .replace(
    'from "lucide-react"',
    `from ${JSON.stringify(import.meta.resolve("lucide-react"))}`,
  )
  .replace(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  )
  .replace(/import "\.\/StrategyResultTabs.css";/g, "");
const { default: Tabs, StrategyRunNotice } = await import(encoded(source));
const frozen = { matched_count: 0, unknown_count: 17, unmatched_count: 1245 };
for (const value of ["matched", "unknown", "unmatched"]) {
  const snapshot = JSON.stringify(frozen);
  const html = renderToStaticMarkup(
    createElement(Tabs, {
      run: frozen,
      value,
      onChange: () => {
        throw new Error("render cannot change a result");
      },
    }),
  );
  assert.equal((html.match(/role="tab"/g) || []).length, 3);
  assert.equal((html.match(/aria-selected="true"/g) || []).length, 1);
  assert.equal((html.match(/tabindex="0"/g) || []).length, 1);
  assert.match(html, /满足/);
  assert.match(html, /待核验/);
  assert.match(html, /未满足/);
  assert.match(html, /<b>0<\/b>/);
  assert.match(html, /<b>17<\/b>/);
  assert.match(html, /<b>1,245<\/b>/);
  assert.equal(JSON.stringify(frozen), snapshot);
}
assert.equal(
  renderToStaticMarkup(
    createElement(StrategyRunNotice, {
      notice: { attention: false, message: "redundant saved metadata" },
    }),
  ),
  "",
);
const stale = renderToStaticMarkup(
  createElement(StrategyRunNotice, {
    notice: {
      attention: true,
      stale: true,
      message: "当前行情日 2026-10-21，当前保留原结果。",
      context: "2026-10-20 V7",
    },
  }),
);
assert.match(stale, /role="status"/);
assert.match(stale, /当前行情日/);
console.log(
  "Strategy result segments: consistent explicit counts/zero, keyboard focus semantics and compact change-only saved-result notices passed",
);
