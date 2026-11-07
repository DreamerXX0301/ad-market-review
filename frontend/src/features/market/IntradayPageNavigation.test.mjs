import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const react = import.meta.resolve("react"),
  runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe = encode(
  `import * as React from ${JSON.stringify(react)};export * from ${JSON.stringify(react)};let ref=0,state=0;export function reset(){ref=0;state=0;}export function useRef(initial){const index=ref++;return React.useRef(index===0?globalThis.__boundary:index===1?globalThis.__content:index===2?globalThis.__menu:initial);}export function useState(initial){const index=state++;return React.useState(index===0?globalThis.__entries:initial);}`,
);
const jsxProbe = encode(
  `import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};export const buttons=[];function see(t,p){if(t==='button')buttons.push(p);}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`,
);
const compile = async (name) =>
  ts.transpileModule(
    await fs.readFile(new URL(name, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const helperSource = encode(await compile("./intradayReadingNavigation.ts"));
const controlsProbe = encode(
  "export const calls=[];export default function Controls(props){calls.push(props);return null;}",
);
const helpers = {
  "../../components/PageScrollEdges": controlsProbe,
  "./emotionNavigation": encode(await compile("./emotionNavigation.ts")),
  "./noonReadingScroll": encode(await compile("./noonReadingScroll.ts")),
  "./intradayReadingNavigation": helperSource,
  react: reactProbe,
  "react/jsx-runtime": jsxProbe,
};
let source = (await compile("./IntradayPageNavigation.tsx")).replace(
  /^import ["'][^"']+\.css["'];?$/gm,
  "",
);
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    match[0],
    "from " +
      JSON.stringify(helpers[match[2]] || import.meta.resolve(match[2])),
  );
const { default: Navigation, renderedIntradaySections } = await import(
  encode(source)
);
const { intradayReadingProps, intradayReadingScrollTop, isIntradayPageScroll } =
  await import(helperSource);
const { buttons } = await import(jsxProbe),
  { reset } = await import(reactProbe);
const { calls: floatingControls } = await import(controlsProbe);
const calls = [];
const document = {
  body: {},
  documentElement: {},
  defaultView: { getComputedStyle: () => ({ overflowY: "auto" }) },
};
const owner = {
  ownerDocument: document,
  parentElement: document.body,
  scrollTop: 400,
  scrollLeft: 87,
  scrollHeight: 4000,
  clientHeight: 900,
  getBoundingClientRect: () => ({ top: 40, bottom: 940 }),
  scrollTo: (options) => calls.push(["owner-scroll", options]),
};
const boundary = {
  parentElement: owner,
  ownerDocument: document,
  closest: () => null,
};
const rows = [];
const content = {
  querySelectorAll: () => rows,
  contains: (node) =>
    node === content || rows.includes(node) || node.parentElement === content,
};
globalThis.__boundary = boundary;
globalThis.__content = content;
globalThis.__menu = { getBoundingClientRect: () => ({ height: 45 }) };
function node(key, options = {}) {
  return {
    ...intradayReadingProps(key),
    dataset: { intradayReading: key },
    firstElementChild: {},
    getClientRects: () => [{}],
    getBoundingClientRect: () => ({ top: 800, bottom: 1000 }),
    setAttribute: (...args) => calls.push(["attribute", ...args]),
    focus: (options) => calls.push(["focus", options]),
    tagName: "SECTION",
    parentElement: content,
    ...options,
  };
}
const brief = node("brief"),
  ladder = node("ladder", { tagName: "DETAILS", open: false });
rows.push(
  brief,
  ladder,
  node("broken", { firstElementChild: null }),
  node("market", { getClientRects: () => [] }),
  node("brief"),
  node("bad"),
);
const available = renderedIntradaySections(content);
assert.deepEqual(
  available.map(({ key }) => key),
  ["brief", "ladder"],
  "only actual nonempty rendered top-level cards enter the directory",
);
assert.equal(intradayReadingScrollTop(800, 400, 56), 1144);
assert.equal(intradayReadingScrollTop(-900, 200, 16), 0);
assert.equal(isIntradayPageScroll(document, document, owner), true);
assert.equal(isIntradayPageScroll(owner, document, owner), true);
assert.equal(
  isIntradayPageScroll({ className: "ibt-scroll" }, document, owner),
  false,
  "internal brief scrolling cannot drive the page directory",
);
assert.equal(
  isIntradayPageScroll({ className: "intraday-table-scroll" }, document, owner),
  false,
);
let reduced = false,
  compact = false;
globalThis.window = {
  innerHeight: 900,
  scrollX: 64,
  scrollY: 200,
  matchMedia: (query) => ({
    matches: query.includes("reduced-motion") ? reduced : compact,
  }),
  scrollTo: (options) => calls.push(["window-scroll", options]),
};
globalThis.requestAnimationFrame = (fn) => {
  fn();
  return 1;
};
function render(entries = available) {
  reset();
  buttons.length = 0;
  globalThis.__entries = entries;
  return renderToStaticMarkup(
    createElement(
      Navigation,
      { scopeKey: "2026-11-05:1400", label: "14:00 午后观察" },
      createElement("section", { id: "contents" }, "已有观察内容"),
    ),
  );
}
let html = render();
assert.deepEqual(
  floatingControls.at(-1).sections.map((row) => row.id),
  [brief.id, ladder.id],
  "floating controls consume the directory’s actual entries",
);
assert.equal(floatingControls.at(-1).onNavigate instanceof Function, true);
assert.match(html, /aria-label="盘中观察页内目录"/);
assert.match(html, /14:00 午后观察/);
assert.match(html, /累计简报/);
assert.match(html, /封板梯队/);
assert.doesNotMatch(
  html,
  /炸板观察|市场概况|href=|role="tab"/,
  "directory never invents missing cards or changes observation routes",
);
floatingControls.at(-1).onNavigate(ladder.id);
assert.equal(
  ladder.open,
  true,
  "clicking a collapsed card opens it before measuring",
);
assert.deepEqual(
  calls.find(([name]) => name === "focus"),
  ["focus", { preventScroll: true }],
);
assert.deepEqual(
  calls.find(([name]) => name === "owner-scroll"),
  ["owner-scroll", { top: 1144, left: 87, behavior: "smooth" }],
  "only the outer scroller moves vertically and horizontal position is preserved",
);
calls.length = 0;
reduced = true;
compact = true;
render();
buttons[0].onClick();
assert.deepEqual(
  calls.find(([name]) => name === "owner-scroll"),
  ["owner-scroll", { top: 1099, left: 87, behavior: "auto" }],
  "compact directory height and reduced motion are respected",
);
calls.length = 0;
boundary.parentElement = document.body;
compact = false;
render();
buttons[0].onClick();
assert.deepEqual(
  calls.find(([name]) => name === "window-scroll"),
  ["window-scroll", { top: 984, left: 64, behavior: "auto" }],
);
html = render([]);
assert.doesNotMatch(html, /<nav/);
assert.match(html, /已有观察内容/);
const original = await fs.readFile(
  new URL("./IntradayPageNavigation.tsx", import.meta.url),
  "utf8",
);
assert.doesNotMatch(
  original,
  /scrollIntoView|location\.hash|setSelected|setRotationBoard/,
);
assert.match(
  original,
  /\}, \[scopeKey, pageActive, pageTitle\]\)/,
  "same-slot API refresh does not reset directory navigation or page position",
);
for (const key of [
  "__boundary",
  "__content",
  "__menu",
  "__entries",
  "requestAnimationFrame",
  "window",
])
  delete globalThis[key];
console.log(
  "Intraday directory: actual cards, disclosure reveal, outer scroll ownership, horizontal preservation, brief isolation, reduced motion and route preservation passed",
);
