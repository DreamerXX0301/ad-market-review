import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

// Exercise the component's actual handlers/effect cleanup with a tiny hook and
// DOM harness. No browser, network, app state or extra dependency is required.
let hooks = [],
  cursor = 0,
  dirty = false,
  effects = [],
  tree;
const equal = (left, right) =>
  left?.length === right?.length &&
  left.every((value, index) => Object.is(value, right[index]));
const useState = (initial) => {
  const index = cursor++;
  hooks[index] ||= {
    value: typeof initial === "function" ? initial() : initial,
  };
  return [
    hooks[index].value,
    (next) => {
      const value =
        typeof next === "function" ? next(hooks[index].value) : next;
      if (!Object.is(value, hooks[index].value)) {
        hooks[index].value = value;
        dirty = true;
      }
    },
  ];
};
const useRef = (value) => {
  const index = cursor++;
  return (hooks[index] ||= { current: value });
};
const useMemo = (factory, deps) => {
  const index = cursor++;
  if (!hooks[index] || !equal(hooks[index].deps, deps))
    hooks[index] = { value: factory(), deps };
  return hooks[index].value;
};
const effect = (callback, deps) => {
  const index = cursor++;
  if (!hooks[index] || !equal(hooks[index].deps, deps)) {
    const old = hooks[index];
    hooks[index] = { deps };
    effects.push(() => {
      old?.cleanup?.();
      hooks[index].cleanup = callback();
    });
  }
};
globalThis.__calendarHooks = {
  useState,
  useRef,
  useMemo,
  useCallback: (fn, deps) => useMemo(() => fn, deps),
  useEffect: effect,
  useLayoutEffect: effect,
  useId: () => useMemo(() => "fixture-calendar", []),
};
class Events {
  listeners = new Map();
  addEventListener(type, callback, options) {
    const capture = options === true || options?.capture === true;
    const key = type + ":" + capture;
    if (!this.listeners.has(key)) this.listeners.set(key, new Set());
    this.listeners.get(key).add(callback);
  }
  removeEventListener(type, callback, options) {
    const capture = options === true || options?.capture === true;
    this.listeners.get(type + ":" + capture)?.delete(callback);
  }
  dispatchEvent(event) {
    for (const capture of [true, false])
      for (const callback of [
        ...(this.listeners.get(event.type + ":" + capture) || []),
      ])
        callback(event);
  }
  listenerCount() {
    return [...this.listeners.values()].reduce(
      (sum, values) => sum + values.size,
      0,
    );
  }
}
class Element extends Events {
  constructor(parentElement = null) {
    super();
    this.parentElement = parentElement;
    this.isConnected = true;
    this.attributes = new Set();
    this.focusCount = 0;
    this.offsetHeight = 475;
  }
  closest() {
    let element = this;
    while (element) {
      if (
        ["hidden", "inert", "aria-hidden"].some((key) =>
          element.attributes.has(key),
        )
      )
        return element;
      element = element.parentElement;
    }
    return null;
  }
  getClientRects() {
    return this.closest() ? [] : [{}];
  }
  getBoundingClientRect() {
    return { top: 100, bottom: 130, left: 20 };
  }
  contains(target) {
    while (target) {
      if (target === this) return true;
      target = target.parentElement;
    }
    return false;
  }
  focus() {
    this.focusCount++;
  }
}
const body = new Element(),
  retainedPage = new Element(body),
  wrapper = new Element(retainedPage),
  trigger = new Element(wrapper),
  popup = new Element(body),
  input = new Element(popup);
globalThis.Node = Element;
globalThis.window = Object.assign(new Events(), {
  innerWidth: 1200,
  innerHeight: 900,
});
globalThis.document = Object.assign(new Events(), { body });
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
const observers = new Set();
globalThis.MutationObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.targets = [];
    observers.add(this);
  }
  observe(target, options) {
    this.targets.push({ target, options });
  }
  disconnect() {
    this.targets = [];
    observers.delete(this);
  }
};
const encoded = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = (source, fileName) =>
  ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const helpers = encoded(
  compile(
    await fs.readFile(new URL("./marketDate.ts", import.meta.url), "utf8"),
    "marketDate.ts",
  ),
);
let source = compile(
  await fs.readFile(new URL("./DatePicker.tsx", import.meta.url), "utf8"),
  "DatePicker.tsx",
);
const mockHooks = encoded(
  "export const {useState,useRef,useMemo,useCallback,useEffect,useLayoutEffect,useId}=globalThis.__calendarHooks;",
);
source = source
  .replaceAll('from "react"', `from ${JSON.stringify(mockHooks)}`)
  .replaceAll(
    'from "react-dom"',
    `from ${JSON.stringify(encoded("export const createPortal=(child)=>child;"))}`,
  )
  .replaceAll('from "./marketDate"', `from ${JSON.stringify(helpers)}`)
  .replace(/import "\.\/MarketDatePicker.css";/g, "")
  .replaceAll(
    'from "lucide-react"',
    `from ${JSON.stringify(import.meta.resolve("lucide-react"))}`,
  )
  .replaceAll(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  );
const { default: DatePicker } = await import(encoded(source));
let changes = [];
let props = {
  value: "2026-10-21",
  min: "2026-10-01",
  max: "2026-10-21",
  onChange: (value) => changes.push(value),
};
function nodes(value, predicate) {
  if (Array.isArray(value))
    return value.flatMap((item) => nodes(item, predicate));
  if (!value || typeof value !== "object" || !value.props) return [];
  return [
    ...(predicate(value) ? [value] : []),
    ...nodes(value.props.children, predicate),
  ];
}
function render() {
  let count = 0;
  do {
    assert.ok(count++ < 20, "effects settle without a render loop");
    dirty = false;
    cursor = 0;
    effects = [];
    tree = DatePicker(props);
    tree.props.ref.current = wrapper;
    nodes(
      tree,
      (node) => node.props["aria-haspopup"] === "dialog",
    )[0].props.ref.current = trigger;
    const dialog = nodes(tree, (node) => node.props.role === "dialog")[0];
    if (dialog) {
      dialog.props.ref.current = popup;
      nodes(dialog, (node) => node.type === "input")[0].props.ref.current =
        input;
    }
    for (const run of effects) run();
  } while (dirty);
  return tree;
}
const isOpen = () =>
  nodes(tree, (node) => node.props.role === "dialog").length === 1;
function open() {
  nodes(
    tree,
    (node) => node.props["aria-haspopup"] === "dialog",
  )[0].props.onClick();
  render();
  assert.ok(isOpen());
}
function mutate(key, present) {
  if (present) retainedPage.attributes.add(key);
  else retainedPage.attributes.delete(key);
  for (const observer of [...observers])
    if (observer.targets.some((item) => item.target === retainedPage))
      observer.callback();
  render();
}
render();
open();
assert.equal(observers.size, 1);
for (const { options } of [...observers][0].targets) {
  assert.equal(
    options.subtree,
    undefined,
    "visibility observation is limited to ancestors, never a whole DOM subtree",
  );
  assert.deepEqual(options.attributeFilter, ["hidden", "inert", "aria-hidden"]);
}
const focusedBefore = trigger.focusCount;
window.dispatchEvent(new Event("hashchange"));
render();
assert.equal(
  isOpen(),
  false,
  "navigating away dismisses a body portal from a retained page",
);
assert.equal(
  trigger.focusCount,
  focusedBefore,
  "navigation must not move focus back into the old route",
);
assert.equal(
  observers.size,
  0,
  "route dismissal releases the ancestor observers",
);
for (const key of ["hidden", "inert", "aria-hidden"]) {
  open();
  const staleDay = nodes(
    tree,
    (node) => node.props["data-calendar-date"] === "2026-10-20",
  )[0];
  mutate(key, true);
  assert.equal(isOpen(), false);
  staleDay.props.onClick();
  render();
  assert.deepEqual(
    changes,
    [],
    "a queued click from a hidden page cannot commit a date",
  );
  nodes(
    tree,
    (node) => node.props["aria-haspopup"] === "dialog",
  )[0].props.onClick();
  render();
  assert.equal(
    isOpen(),
    false,
    "a hidden retained page cannot reopen its portal",
  );
  mutate(key, false);
}
for (const next of [
  { value: "2026-10-20" },
  { min: "2026-10-10" },
  { max: "2026-10-20" },
  { allowedDates: ["2026-10-10", "2026-10-20"] },
]) {
  open();
  nodes(tree, (node) => node.type === "input")[0].props.onChange({
    target: { value: "2026-10-11" },
  });
  render();
  props = { ...props, ...next };
  render();
  assert.equal(
    isOpen(),
    false,
    "a new value or selectable scope discards the stale draft",
  );
  open();
  assert.equal(
    nodes(tree, (node) => node.type === "input")[0].props.value,
    props.value,
    "reopening uses the latest selected value",
  );
  window.dispatchEvent(new Event("hashchange"));
  render();
}
open();
props = { ...props, allowedDates: [...props.allowedDates] };
render();
assert.equal(
  isOpen(),
  true,
  "equal allowed-date contents do not close the calendar on an ordinary parent render",
);
props = { ...props, disabled: true };
render();
assert.equal(isOpen(), false);
for (const hook of hooks) hook.cleanup?.();
assert.equal(observers.size, 0);
assert.equal(
  window.listenerCount(),
  0,
  "unmount releases route, keyboard, scroll and resize listeners",
);
assert.equal(
  document.listenerCount(),
  0,
  "unmount releases outside-focus and pointer listeners",
);
console.log(
  "Date picker lifecycle: route/retained visibility cleanup, stale-click guards and changed-value/range draft isolation passed",
);
