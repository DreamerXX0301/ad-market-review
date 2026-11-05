import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const probe = encode(
  `import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};export const buttons=[];function see(t,p){if(t==='button')buttons.push(p);}export function jsx(t,p,k){see(t,p);return a(t,p,k);}export function jsxs(t,p,k){see(t,p);return b(t,p,k);}`,
);
let source = ts
  .transpileModule(
    await fs.readFile(
      new URL("./FloatingScrollControls.tsx", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  )
  .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
source = source.replace(
  'from "react/jsx-runtime"',
  "from " + JSON.stringify(probe),
);
const { default: Controls } = await import(encode(source));
const { buttons } = await import(probe);
const calls = [];
const action = (caption, label, disabled = false, shortcut) => ({
  caption,
  label,
  disabled,
  shortcut,
  icon: createElement("i"),
  onClick: () => calls.push(label),
});
let html = renderToStaticMarkup(
  createElement(Controls, {
    label: "盘中观察快速滚动",
    previous: action("顶部", "回到页面顶部", true),
    next: action("底部", "回到页面底部"),
  }),
);
assert.match(html, /data-position="edges"/);
assert.match(html, /aria-label="回到页面顶部"[^>]*disabled=""/);
assert.match(html, /floating-scroll-caption">顶部/);
assert.match(html, /floating-scroll-caption">底部/);
assert.doesNotMatch(html, /floating-scroll-position/);
buttons[1].onClick();
assert.deepEqual(calls, ["回到页面底部"]);
buttons.length = 0;
html = renderToStaticMarkup(
  createElement(Controls, {
    label: "午评上下节快捷导航",
    previous: action("上节", "午评上一节", false, "Alt+ArrowUp"),
    next: action("下节", "午评下一节", true, "Alt+ArrowDown"),
    position: { text: "6/6", label: "当前第6节，共6节" },
  }),
);
assert.match(html, /data-position="sections"/);
assert.match(html, /aria-keyshortcuts="Alt\+ArrowUp"/);
assert.match(html, /aria-label="当前第6节，共6节">6\/6/);
assert.match(html, /floating-scroll-caption">上节/);
assert.doesNotMatch(
  html,
  /回到页面顶部|回到页面底部/,
  "section navigation retains its original meaning rather than becoming page-edge navigation",
);
buttons[0].onClick();
assert.deepEqual(calls, ["回到页面底部", "午评上一节"]);
const css = await fs.readFile(
  new URL("./FloatingScrollControls.css", import.meta.url),
  "utf8",
);
assert.match(css, /width: 40px/);
assert.match(css, /:hover:not\(:disabled\)/);
assert.match(css, /:focus-visible/);
assert.match(css, /prefers-reduced-motion/);
assert.match(css, /safe-area-inset-bottom/);
for (const file of [
  "../features/market/NoonReview.css",
  "../features/market/NoonCloseReview.css",
])
  assert.doesNotMatch(
    await fs.readFile(new URL(file, import.meta.url), "utf8"),
    /\.noon-(?:quick-navigation|close-quick)\s*\{/,
  );
console.log(
  "Floating navigation: shared appearance, distinct page/section actions, original shortcuts, count and disabled states passed",
);
