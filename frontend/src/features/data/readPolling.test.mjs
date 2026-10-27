import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./DataCenter.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "DataCenter.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const effects = [];
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(ast) === "useEffect")
    effects.push(node.arguments[0].getText(ast));
  ts.forEachChild(node, visit);
}
visit(ast);
for (const [name, delay] of [
  ["task", 2000],
  ["history", 2000],
  ["result", 60000],
]) {
  const body = effects.find((value) =>
    value.includes(`setTimeout(${name}.reload, ${delay})`),
  );
  assert.ok(body, `${name} polling effect is exercised from the component`);
  const timers = [],
    cancelled = [];
  const scope = {
    active: false,
    task: { data: { status: "running" }, reload() {} },
    history: { reload() {} },
    result: { reload() {} },
    followChain: true,
    chain: { state: "running" },
    publishedStatuses: ["completed"],
    selectedActive: true,
    waitingForChild: false,
    activeHistory: true,
    pendingHistoryChain: false,
    setSelectedId() {
      throw Error("hidden page must not follow a task chain");
    },
    setTimeout(fn, ms) {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimeout(id) {
      cancelled.push(id);
    },
  };
  const run = () =>
    Function(
      ...Object.keys(scope),
      `return (${body})();`,
    )(...Object.values(scope));
  assert.equal(run(), undefined);
  assert.equal(
    timers.length,
    0,
    `${name} stops while its page/document is hidden`,
  );
  scope.active = true;
  const cleanup = run();
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, delay);
  cleanup();
  assert.deepEqual(
    cancelled,
    [1],
    `${name} removes the pending timer on hide/unmount`,
  );
}

const tracking = await fs.readFile(
  new URL("../tracking/Tracking.tsx", import.meta.url),
  "utf8",
);
const trackingAst = ts.createSourceFile(
  "Tracking.tsx",
  tracking,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
let eventPath;
function find(node) {
  if (
    ts.isVariableDeclaration(node) &&
    node.name.getText(trackingAst) === "eventsResult"
  )
    eventPath = node.initializer.arguments[0].getText(trackingAst);
  ts.forEachChild(node, find);
}
find(trackingAst);
assert.ok(eventPath);
const path = Function("tab", "strategyId", `return (${eventPath});`);
assert.equal(path("active", "one"), null);
assert.equal(path("history", "one"), null);
assert.equal(
  path("alerts", "one"),
  "/tracking-events?limit=200&strategy_id=one&event_type=alert",
);
assert.equal(path("alerts", ""), "/tracking-events?limit=200&event_type=alert");
console.log(
  "data polling and tracking event reads follow the active page and selected tab",
);
