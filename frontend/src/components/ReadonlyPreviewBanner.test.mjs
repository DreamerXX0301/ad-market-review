import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const source = (
  await fs.readFile(
    new URL("./ReadonlyPreviewBanner.tsx", import.meta.url),
    "utf8",
  )
).replace('import "./ReadonlyPreviewBanner.css";', "");
const compiled = ts
  .transpileModule(source, {
    fileName: "banner.tsx",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  .outputText.replaceAll(
    '"react/jsx-runtime"',
    JSON.stringify(import.meta.resolve("react/jsx-runtime")),
  );
const { default: Banner } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
assert.equal(renderToStaticMarkup(createElement(Banner, {})), "");
assert.equal(
  renderToStaticMarkup(createElement(Banner, { enabled: false })),
  "",
);
const html = renderToStaticMarkup(createElement(Banner, { enabled: true }));
assert.ok(html.includes("只读验收预览") && html.includes('role="status"'));
assert.ok(html.includes("预览服务限制"));
console.log(
  "readonly preview banner is opt-in and never labels an ordinary build readonly",
);
