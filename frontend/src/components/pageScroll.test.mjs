import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./pageScroll.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { pageScrollOwner, pageScrollState, scrollPageEdge } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const calls = [];
let reduced = false;
const document = {
  body: {},
  documentElement: { scrollHeight: 3000 },
  defaultView: {
    innerHeight: 800,
    scrollX: 37,
    getComputedStyle: (node) => ({ overflowY: node.overflow }),
    matchMedia: () => ({ matches: reduced }),
    scrollTo: (options) => calls.push(["document", options]),
  },
};
const main = {
  overflow: "visible",
  parentElement: document.body,
  ownerDocument: document,
  scrollTop: 100,
  scrollLeft: 29,
  scrollHeight: 2500,
  clientHeight: 700,
  scrollTo: (options) => calls.push(["main", options]),
};
const page = { parentElement: main, ownerDocument: document };
assert.equal(
  pageScrollOwner(page),
  null,
  "current AppShell uses the document viewport",
);
scrollPageEdge(page, "bottom");
assert.deepEqual(calls.pop(), [
  "document",
  { top: 2200, left: 37, behavior: "smooth" },
]);
main.overflow = "auto";
assert.equal(
  pageScrollOwner(page),
  main,
  "constrained main containers own their page",
);
scrollPageEdge(page, "bottom");
assert.deepEqual(calls.pop(), [
  "main",
  { top: 1800, left: 29, behavior: "smooth" },
]);
reduced = true;
scrollPageEdge(page, "top");
assert.deepEqual(calls.pop(), ["main", { top: 0, left: 29, behavior: "auto" }]);
main.scrollHeight = main.clientHeight;
assert.equal(
  pageScrollOwner(page),
  null,
  "declarations without overflow do not steal scroll",
);
assert.equal(pageScrollState(0, 3000, 800).atTop, true);
assert.equal(pageScrollState(2200, 3000, 800).atBottom, true);
assert.equal(pageScrollState(120, 3000, 800).visible, true);
assert.equal(
  pageScrollState(0, 800, 800).visible,
  false,
  "short pages do not need controls",
);
assert.equal(pageScrollState(NaN, 3000, 800).visible, false);
console.log(
  "page scroll edges: document/main ownership, both ends, horizontal preservation and reduced motion passed",
);
