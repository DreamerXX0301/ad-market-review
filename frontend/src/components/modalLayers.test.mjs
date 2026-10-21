import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./modalLayers.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { createModalLayers, canRestoreModalFocus } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const stack = createModalLayers();
let notifications = 0;
const unsubscribe = stack.subscribe(() => notifications++);
const removeParent = stack.register("parent");
const removeChild = stack.register("child", "parent");
assert.equal(stack.top(), "child");
const escape = {};
assert.equal(stack.isTopForEvent("child", escape), true);
removeChild();
assert.equal(stack.top(), "parent");
assert.equal(
  stack.isTopForEvent("parent", escape),
  false,
  "one Escape must not also close the newly revealed parent",
);
assert.equal(stack.isTopForEvent("parent", {}), true);
const removeNext = stack.register("next", "parent");
const parentFirstEvent = {};
assert.equal(stack.isTopForEvent("parent", parentFirstEvent), false);
assert.equal(
  stack.isTopForEvent("next", parentFirstEvent),
  true,
  "listener order must not change which layer closes",
);
removeNext();
removeParent();
assert.equal(stack.top(), null);
unsubscribe();
const count = notifications;
const cleanup = stack.register("unsubscribed");
cleanup();
assert.equal(notifications, count);
const nested = createModalLayers();
const removeGrandchild = nested.register("grandchild", "child");
const removeInitialChild = nested.register("child", "parent");
const removeInitialParent = nested.register("parent");
assert.equal(
  nested.top(),
  "grandchild",
  "child-first React passive effects cannot promote the parent above its nested child",
);
const removeSibling = nested.register("sibling");
assert.equal(nested.top(), "sibling");
removeSibling();
assert.equal(nested.top(), "grandchild");
removeInitialParent();
assert.equal(nested.top(), "grandchild");
removeGrandchild();
assert.equal(nested.top(), "child");
removeInitialChild();
assert.equal(nested.top(), null);
const strict = createModalLayers();
const oldCleanup = strict.register("same");
oldCleanup();
const currentCleanup = strict.register("same");
oldCleanup();
assert.equal(
  strict.has("same"),
  true,
  "StrictMode stale cleanup cannot unregister the replayed live layer",
);
const staleCleanup = strict.register("same");
currentCleanup();
assert.equal(strict.has("same"), true);
staleCleanup();
assert.equal(strict.top(), null);
const focusable = {
  isConnected: true,
  focus() {},
  closest() {
    return null;
  },
  getClientRects() {
    return [{}];
  },
};
assert.equal(canRestoreModalFocus(focusable), true);
for (const element of [
  null,
  { ...focusable, isConnected: false },
  {
    ...focusable,
    closest() {
      return {};
    },
  },
  {
    ...focusable,
    getClientRects() {
      return [];
    },
  },
  { ...focusable, focus: null },
])
  assert.equal(canRestoreModalFocus(element), false);
assert.equal(
  canRestoreModalFocus(focusable, {
    contains() {
      return false;
    },
  }),
  false,
  "do not restore focus behind a newly opened modal",
);
assert.equal(
  canRestoreModalFocus(focusable, {
    contains() {
      return true;
    },
  }),
  true,
);
console.log(
  "modal layers: nested/StrictMode order, one-event one-close, sibling cleanup, subscriptions and connected visible focus restoration passed",
);
