import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source) =>
  ts
    .transpileModule(source, {
      fileName: "modal.tsx",
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
const helpers = encode(
  compile(
    await fs.readFile(new URL("./modalLayers.ts", import.meta.url), "utf8"),
  ),
);
const source = await fs.readFile(new URL("./UI.tsx", import.meta.url), "utf8");
const modal = source.slice(
  source.indexOf("export function Modal("),
  source.indexOf("export type Column ="),
);
const slots = [];
let cursor = 0,
  dirty = false,
  effects = [],
  frames = [],
  hosts = [],
  drawer = null,
  focusTarget = null;
const events = new Map();
globalThis.HTMLElement = class {};
const same = (a, b) =>
  a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
function element(name, attrs = {}) {
  return Object.assign(new globalThis.HTMLElement(), {
    name,
    isConnected: true,
    dataset: { modalLayer: attrs["data-modal-layer"] },
    focus() {
      focusTarget = this;
      globalThis.document.activeElement = this;
    },
    closest() {
      return attrs.inert || attrs["aria-hidden"] ? {} : null;
    },
    getClientRects() {
      return attrs.inert ? [] : [{}];
    },
    contains(target) {
      return target === this || target?.scope === this;
    },
    getAttribute() {
      return null;
    },
    removeAttribute() {},
    setAttribute() {},
  });
}
const opener = element("alert list button"),
  main = element("main");
globalThis.document = {
  activeElement: opener,
  body: element("body"),
  querySelector(selector) {
    return selector === ".stock-drawer"
      ? drawer
      : selector === "main"
        ? main
        : null;
  },
  querySelectorAll() {
    return hosts;
  },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = {
  addEventListener(type, fn) {
    if (!events.has(type)) events.set(type, new Set());
    events.get(type).add(fn);
  },
  removeEventListener(type, fn) {
    events.get(type)?.delete(fn);
  },
};
globalThis.requestAnimationFrame = (fn) => frames.push(fn);
globalThis.__modalHandoffHooks = {
  useContext: () => null,
  useId: () => "reminder-modal",
  useRef(value) {
    const i = cursor++;
    return (slots[i] ??= { value: { current: value } }).value;
  },
  useState(initial) {
    const i = cursor++;
    const slot = (slots[i] ??= {
      value: typeof initial === "function" ? initial() : initial,
    });
    return [
      slot.value,
      (next) => {
        const value = typeof next === "function" ? next(slot.value) : next;
        if (!Object.is(value, slot.value)) {
          slot.value = value;
          dirty = true;
        }
      },
    ];
  },
  useEffect(callback, deps) {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps)) {
      const previous = slots[i];
      slots[i] = { deps, cleanup: previous?.cleanup };
      effects.push(() => {
        slots[i].cleanup?.();
        slots[i].cleanup = callback();
      });
    }
  },
};
const prelude = `import {createModalLayers,canRestoreModalFocus} from ${JSON.stringify(helpers)};const {useContext,useId,useRef,useState,useEffect}=globalThis.__modalHandoffHooks;const modalLayers=createModalLayers();const ModalParentContext={Provider:()=>null};const X=()=>null;const STOCK_DRAWER_VISIBILITY_EVENT="workbench-stock-drawer-visibility";`;
const { Modal } = await import(encode(compile(prelude + modal)));
let closeCount = 0;
const props = {
  title: "划线提醒",
  escapeUnderStock: true,
  onClose: () => closeCount++,
};
function attach(node) {
  if (!node || typeof node !== "object") return;
  if (typeof node.type === "string" && node.props.ref) {
    const host = element(node.type, node.props);
    node.props.ref.current = host;
    if (node.props["data-modal-layer"]) hosts.push(host);
  }
  const children = node.props?.children;
  for (const child of Array.isArray(children) ? children : [children])
    attach(child);
}
function render() {
  let tree;
  let n = 0;
  do {
    dirty = false;
    cursor = 0;
    hosts = [];
    tree = Modal(props);
    attach(tree);
    for (const effect of effects.splice(0)) effect();
    assert.ok(++n < 12);
  } while (dirty);
  return tree.props.children;
}
let backdrop = render();
assert.notEqual(backdrop.props.style?.display, "none");
drawer = element("stock drawer");
for (const fn of events.get("workbench-stock-drawer-visibility"))
  fn({ detail: { open: true } });
backdrop = render();
assert.equal(
  backdrop.props.hidden,
  true,
  "a suspended modal must leave the visual layer as well as the accessibility layer",
);
assert.equal(
  backdrop.props.style?.display,
  "none",
  "display:flex stylesheet must not override the hidden handoff backdrop",
);
const suspendedModal = backdrop.props.children;
assert.equal(suspendedModal.props.inert, true);
// Returning from a protected-stock preview reveals the preserved dialog with a working close button.
drawer = null;
for (const fn of events.get("workbench-stock-drawer-visibility"))
  fn({ detail: { open: false } });
backdrop = render();
assert.notEqual(backdrop.props.style?.display, "none");
backdrop.props.children.props.children[0].props.children[1].props.onClick();
assert.equal(closeCount, 1);
// A close-and-open-chart handoff must not restore focus to the list behind the drawer.
drawer = element("new stock drawer");
const drawerClose = element("drawer close");
drawerClose.scope = drawer;
drawerClose.focus();
for (const slot of slots) slot?.cleanup?.();
hosts = [];
for (const fn of frames.splice(0)) fn();
assert.equal(
  focusTarget,
  drawerClose,
  "modal cleanup cannot steal focus behind the newly opened stock drawer",
);
delete globalThis.__modalHandoffHooks;
console.log(
  "modal stock handoff: suspended backdrop hidden, returning dialog closes and navigation focus remains inside drawer passed",
);
