import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, tsx = false) => ts.transpileModule(source, {
  fileName: tsx ? "component.tsx" : "module.ts",
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const helperUrl = encode(compile(await fs.readFile(new URL("./emotionNavigation.ts", import.meta.url), "utf8")));
const { emotionSections, revealEmotionSection, activeEmotionSection, keepEmotionSelection } = await import(helperUrl);
const sections = emotionSections({ outlook: true, statistics: true, history: true });
assert.equal(sections.length, 8);
assert.equal(new Set(sections.map((row) => row.id)).size, 8);
assert.deepEqual(emotionSections({ outlook: false, statistics: false, history: true }), [{ id: "emotion-history", label: "广度历史" }]);
assert.deepEqual(emotionSections({ outlook: false, statistics: false, history: false }), []);

const order = [];
const boundary = { contains: (element) => element === target || element === inner || element === outer };
const outer = { tagName: "DETAILS", parentElement: boundary, set open(value) { order.push(["outer", value]); } };
const inner = { tagName: "DETAILS", parentElement: outer, set open(value) { order.push(["inner", value]); } };
const target = { tagName: "SECTION", parentElement: inner };
assert.equal(revealEmotionSection(target, boundary), true);
assert.deepEqual(order, [["outer", true], ["inner", true]], "nested disclosures open from outside in before layout is measured");
assert.equal(revealEmotionSection({}, boundary), false, "a target outside this market page must not be opened");
order.length = 0;
assert.equal(revealEmotionSection(inner, boundary), true);
assert.deepEqual(order, [["outer", true], ["inner", true]], "a history details target opens itself too");
assert.equal(activeEmotionSection([{ id: "closed", top: 0, visible: false }, { id: "reading", top: -20, visible: true }, { id: "plan", top: 300, visible: true }], 100), "reading");
assert.equal(activeEmotionSection([{ id: "reading", top: -400, visible: true }, { id: "plan", top: 90, visible: true }, { id: "hidden", top: 0, visible: false }], 100), "plan");
assert.equal(activeEmotionSection([{ id: "reading", top: 200, visible: true }], 100), "reading");
assert.equal(activeEmotionSection([{ id: "history", top: 80, visible: true }, { id: "sources", top: -200, visible: true }], 100), "history", "active item follows actual page geometry even when sections are grouped differently from the menu");
assert.equal(activeEmotionSection([{ id: "missing", top: NaN, visible: true }], 100), null);
const bottom = [{ offset: 500, limit: 500 }, { offset: 0, limit: 0 }];
assert.equal(keepEmotionSelection(bottom, structuredClone(bottom), true), true, "a visible clicked section stays selected across a child re-render with no scroll movement");
assert.equal(keepEmotionSelection(bottom, [{ offset: 420, limit: 420 }, { offset: 0, limit: 0 }], true), true, "layout shrinking at the clamped bottom keeps the selected visible section");
assert.equal(keepEmotionSelection(bottom, [{ offset: 700, limit: 700 }, { offset: 0, limit: 0 }], true), true, "a new clamped destination after opening details remains anchored");
assert.equal(keepEmotionSelection(bottom, [{ offset: 700, limit: 700 }, { offset: 0, limit: 0 }], true, false), false, "actual movement after a manual gesture releases the selection even if it reaches another clamped endpoint");
assert.equal(keepEmotionSelection(bottom, bottom, true, false), true, "a manual gesture that cannot move beyond an edge does not reset the clicked section");
assert.equal(keepEmotionSelection(bottom, [{ offset: 450, limit: 500 }, { offset: 0, limit: 0 }], true), false, "real scrolling away from the recorded destination restores scroll-spy");
assert.equal(keepEmotionSelection(bottom, [{ offset: 499, limit: 500 }, { offset: 0, limit: 0 }], true), true);
assert.equal(keepEmotionSelection(bottom, [{ offset: 497, limit: 500 }, { offset: 0, limit: 0 }], true), false, "small moves accumulate against the original anchor rather than walking the lock along with user scrolling");
assert.equal(keepEmotionSelection(bottom, bottom, false), false, "a hidden/offscreen clicked section cannot remain active");
assert.equal(keepEmotionSelection([{ offset: 200, limit: 700 }], [{ offset: 300, limit: 700 }], true), false, "mid-page movement is not confused with a clamped endpoint");
assert.equal(keepEmotionSelection([{ offset: 0, limit: 0 }, { offset: 100, limit: 400 }], [{ offset: 0, limit: 0 }, { offset: 160, limit: 400 }], true), false, "nested user scrolling is detected even when the document itself cannot scroll");
assert.equal(keepEmotionSelection(bottom, [{ offset: NaN, limit: 500 }, { offset: 0, limit: 0 }], true), false);

let code = compile(await fs.readFile(new URL("./EmotionPageNavigation.tsx", import.meta.url), "utf8"), true);
code = code.replaceAll('from "react"', "from " + JSON.stringify(import.meta.resolve("react"))).replaceAll('from "react/jsx-runtime"', "from " + JSON.stringify(import.meta.resolve("react/jsx-runtime"))).replaceAll('from "./emotionNavigation"', "from " + JSON.stringify(helperUrl)).replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const { default: Navigation } = await import(encode(code));
const markup = renderToStaticMarkup(createElement(Navigation, { date: "2026-10-23", outlook: true, statistics: true, history: true }, createElement("section", { id: "emotion-reading" }, "当前读数")));
assert.ok(markup.includes('aria-label="市场情绪页内目录"'));
assert.ok(markup.includes('aria-current="location"'));
assert.ok(markup.includes("2026-10-23"));
for (const { id, label } of sections) {
  assert.ok(markup.includes('aria-controls="' + id + '"'));
  assert.ok(markup.includes(label));
}
assert.ok(!markup.includes("href="), "navigation must preserve the application's hash route and shared date");
assert.ok(!markup.includes('role="tab"'), "in-page sections stay available together, rather than pretending to be mutually exclusive tabs");
console.log("emotion navigation: scoped nested disclosure, visible scroll-spy, available sections and hash-safe accessible controls passed");
