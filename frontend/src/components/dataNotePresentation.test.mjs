import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
async function compiled(name, jsx = false) {
  return ts.transpileModule(
    await fs.readFile(new URL(name, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}),
      },
    },
  ).outputText;
}
const helper = encode(await compiled("./dataNotePresentation.ts"));
const { dataNotesModel, chartDataNotes } = await import(helper);

const common = ["来源字段待核验。", "年内来源池有缺口；未知不补0。"];
const grouped = dataNotesModel([
  common,
  ["来源字段待核验", null, { message: "年内来源池有缺口；未知不补0。" }],
]);
assert.equal(grouped.notes.length, 2);
assert.deepEqual(grouped.issues, []);
assert.match(grouped.notes[1].message, /未知不补0/);
const escalated = dataNotesModel([
  "目标日数据校验失败。",
  { message: "目标日数据校验失败", severity: "error" },
  { reason: "当前请求缺少必要身份", blocking: true },
  { message: "供应商仅提供有限窗口", severity: "info" },
]);
assert.equal(escalated.notes.length, 1);
assert.equal(
  escalated.issues.length,
  2,
  "explicit blockers must not be folded or lost to deduplication",
);
assert.equal(dataNotesModel(undefined).notes.length, 0);
const merged = dataNotesModel(
  chartDataNotes(
    ["未复权原始价格；公司行动附近指标重新预热。", ...common],
    ["未复权原始价格；未声称完整复权已验收。", ...common],
  ),
);
assert.equal(
  merged.notes.length,
  3,
  "chart/detail source notes are merged once",
);
assert.match(merged.notes[0].message, /基础行情保存未复权/);
assert.match(merged.notes[0].message, /策略、跟踪和回测口径独立/);

let component = await compiled("./DataNotes.tsx", true);
component = component
  .replaceAll(
    '"react/jsx-runtime"',
    JSON.stringify(import.meta.resolve("react/jsx-runtime")),
  )
  .replaceAll(
    '"lucide-react"',
    JSON.stringify(import.meta.resolve("lucide-react")),
  )
  .replaceAll('"./dataNotePresentation"', JSON.stringify(helper))
  .replace('import "./DataNotes.css";', "");
const { default: DataNotes } = await import(encode(component));
const markup = renderToStaticMarkup(
  createElement(DataNotes, {
    items: [
      common,
      common,
      { message: "缺少必要价格，操作已阻断", severity: "error" },
    ],
  }),
);
assert.match(markup, /数据说明 · 2项/);
assert.match(markup, /来源范围、计算口径与已知缺口/);
assert.match(
  markup,
  /<details class="data-notes-fold">/,
  "source notes default to collapsed",
);
assert.equal((markup.match(/来源字段待核验。/g) || []).length, 1);
assert.ok(markup.indexOf('role="alert"') < markup.indexOf("<details"));
assert.ok(
  markup.indexOf("缺少必要价格，操作已阻断") < markup.indexOf("<details"),
);
assert.equal(renderToStaticMarkup(createElement(DataNotes, { items: [] })), "");
console.log(
  "Data notes: source deduplication, full text, default fold, basis wording and always-visible blockers passed",
);
