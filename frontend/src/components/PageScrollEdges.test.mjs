import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = async (name) => ts.transpileModule(await fs.readFile(new URL(name, import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const react = import.meta.resolve("react"), runtime = import.meta.resolve("react/jsx-runtime");
const hooks = encode(`import * as React from ${JSON.stringify(react)};export * from ${JSON.stringify(react)};let state=0;export function reset(){state=0;}export function useRef(){return React.useRef(globalThis.__sectionMarker);}export function useState(initial){return React.useState(state++===0?globalThis.__sectionDocument:true);}`);
const jsxProbe = encode(`import {jsx as a,jsxs as b} from ${JSON.stringify(runtime)};export {Fragment} from ${JSON.stringify(runtime)};export const buttons=[];const see=(t,p)=>{if(t==='button')buttons.push(p);};export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`);
const navigationUrl = encode(await compile("./sectionNavigation.ts"));
const floatingUrl = encode((await compile("./FloatingScrollControls.tsx")).replace('from "react/jsx-runtime"', "from " + JSON.stringify(jsxProbe)));
let source = await compile("./PageScrollEdges.tsx");
const imports = { react: hooks, "react-dom": encode("export const createPortal=(value)=>value;"), "react/jsx-runtime": jsxProbe, "./FloatingScrollControls": floatingUrl, "./sectionNavigation": navigationUrl };
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(match[0], "from " + JSON.stringify(imports[match[2]] || import.meta.resolve(match[2])));
const { default: Edges } = await import(encode(source));
const { sectionNavigation } = await import(navigationUrl);
const { reset } = await import(hooks), { buttons } = await import(jsxProbe);
let modal = false;
const document = { body: {}, querySelectorAll: () => modal ? [{ getClientRects: () => [{}] }] : [] };
globalThis.__sectionDocument = document;
globalThis.__sectionMarker = { ownerDocument: document };
const sections = [{ id: "brief", label: "累计简报" }, { id: "broken", label: "炸板" }, { id: "rotation", label: "题材轮动" }];
const calls = [];
function render(activeId, entries = sections) {
  reset(); buttons.length = 0;
  return renderToStaticMarkup(createElement(Edges, { label: "盘中观察", sections: entries, activeId, onNavigate: (id) => calls.push(id) }));
}
let html = render("brief");
assert.match(html, /data-position="sections"/);
assert.match(html, /当前第 1 节，共 3 节：累计简报/);
assert.equal(buttons[0].disabled, true);
assert.equal(buttons[1].disabled, false);
buttons[1].onClick();
assert.deepEqual(calls, ["broken"], "next uses the directory's next real section");
html = render("broken");
assert.match(html, />2\/3</);
buttons[0].onClick(); buttons[1].onClick();
assert.deepEqual(calls, ["broken", "brief", "rotation"]);
render("rotation");
assert.equal(buttons[1].disabled, true);
assert.match(html, /上节|下节/);
assert.doesNotMatch(html, /回到顶部|回到底部|页面顶部|页面底部/);
modal = true; buttons[0].onClick();
assert.equal(calls.length, 3, "a modal blocks chapter navigation even before the visibility observer runs");
modal = false;
assert.doesNotMatch(render("brief", []), /floating-scroll-controls/);
assert.doesNotMatch(render("brief", sections.slice(0, 1)), /floating-scroll-controls/);
assert.deepEqual(sectionNavigation([sections[0], sections[0], { id: "", label: "未渲染" }, sections[2]], "rotation").entries, [sections[0], sections[2]]);
assert.equal(sectionNavigation(sections, "removed").index, 0);
assert.equal(sectionNavigation([sections[0], sections[2]], "brief").next.id, "rotation", "removed cards are not counted or navigated");
assert.doesNotMatch(source, /scrollPageEdge|scrollIntoView|setActive|setPosition/, "the floating control does not introduce a second scroll-spy or its own page scrolling");
assert.match(source, /pageHasModal/);
assert.match(source, /parentElement\?\.getClientRects/, "hidden keep-alive pages do not leave floating controls behind");
delete globalThis.__sectionDocument; delete globalThis.__sectionMarker;
console.log("Section controls: shared directory selection, previous/next, count, endpoints, missing cards and modal guard passed");
