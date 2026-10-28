import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(new URL("./strategyBatches.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  batchActive,
  batchPolling,
  batchRequest,
  nextStrategySelection,
  mergeBatch,
  createBatchFeed,
  batchTrackingSummary,
  batchItemTrackingText,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
let keys = 0;
const key = () => `request-${++keys}`;
const first = batchRequest(
  null,
  "stock",
  "selected",
  ["b", "a", "a"],
  "2026-10-21",
  key,
);
assert.deepEqual(first.body, {
  target_type: "stock",
  scope: "selected",
  strategy_ids: ["b", "a"],
  request_key: "request-1",
});
assert.equal(
  batchRequest(first, "stock", "selected", ["b", "a"], "2026-10-21", key),
  first,
  "uncertain POST retries preserve the exact request key",
);
for (const [target, scope, ids, day] of [
  ["stock", "selected", ["a", "b"], "2026-10-21"],
  ["sector", "selected", ["a", "b"], "2026-10-21"],
  ["stock", "selected", ["a"], "2026-10-21"],
  ["stock", "all", ["a", "b"], "2026-10-21"],
  ["stock", "selected", ["a", "b"], "2026-10-22"],
]) {
  assert.notEqual(
    batchRequest(first, target, scope, ids, day, key).body.request_key,
    first.body.request_key,
  );
}
assert.equal(
  "strategy_ids" in
    batchRequest(null, "sector", "all", ["a"], "2026-10-21", key).body,
  false,
  "all means the server freezes the current target universe",
);
assert.equal(
  "auto_add" in first.body,
  false,
  "new auto-follow semantics are frozen by the server, never retrofitted into an old request",
);
for (const legacy of [
  {},
  { auto_add: false },
  { items: [{ added_count: 99 }] },
]) {
  const result = batchTrackingSummary(legacy);
  assert.equal(result.automatic, false);
  assert.match(result.label, /仅筛选/);
  assert.match(result.text, /未自动/);
  assert.doesNotMatch(result.text, /99/);
}
assert.match(
  batchTrackingSummary({ auto_add: true, items: [] }).text,
  /待回报/,
);
assert.match(
  batchTrackingSummary({
    auto_add: true,
    items: [
      { added_count: 0, skipped_count: 3, ignored_count: 1 },
      { added_count: 2, skipped_count: 0, ignored_count: 0 },
    ],
  }).text,
  /新增跟踪 2.*已跳过 3.*手工忽略 1/,
);
assert.match(
  batchTrackingSummary({
    auto_add: true,
    added_count: 0,
    items: [{ added_count: 20 }],
  }).text,
  /新增跟踪 0/,
);
assert.match(
  batchItemTrackingText(
    {
      selection_status: "succeeded",
      tracking_status: "failed",
      matched_count: 5,
    },
    true,
  ),
  /筛选：已完成.*入池：失败.*命中 5/,
);
assert.equal(
  batchItemTrackingText({ added_count: 99 }, false),
  "仅筛选，未自动入池",
);
const candidates = new Set(["stock-1"]),
  selected = new Set(["strategy-1"]);
const all = nextStrategySelection(selected, ["strategy-1", "strategy-2"], true);
assert.deepEqual([...all], ["strategy-1", "strategy-2"]);
assert.deepEqual([...selected], ["strategy-1"]);
assert.deepEqual(
  [...candidates],
  ["stock-1"],
  "strategy multiselect never touches candidate selection",
);
assert.equal(
  nextStrategySelection(all, ["strategy-1", "strategy-2"], false).size,
  0,
);
assert.equal(
  mergeBatch([{ batch_id: "old" }], { batch_id: "new" })[0].batch_id,
  "new",
);
assert.equal(
  mergeBatch([{ batch_id: "same", status: "queued" }], {
    batch_id: "same",
    status: "running",
  }).length,
  1,
);
for (const status of ["queued", "running", "paused"])
  assert.equal(batchActive({ status }), true);
for (const status of ["succeeded", "partial", "failed", "cancelled"])
  assert.equal(batchActive({ status }), false);
assert.equal(
  batchPolling([{ status: "paused" }]),
  false,
  "paused batches wait for a manual resume/refresh",
);

const pending = [],
  timers = new Map(),
  observed = [],
  errors = [],
  progress = [];
let sequence = 0,
  reads = 0,
  inFlight = 0,
  maxInFlight = 0;
const feed = createBatchFeed({
  read: (signal) => {
    reads++;
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    return new Promise((resolve, reject) =>
      pending.push({
        signal,
        resolve: (value) => {
          inFlight--;
          resolve(value);
        },
        reject: (error) => {
          inFlight--;
          reject(error);
        },
      }),
    );
  },
  onData: (data) => observed.push(data),
  onError: (error) => errors.push(error),
  onProgress: () => progress.push(true),
  schedule: (callback, delay) => {
    const id = ++sequence;
    timers.set(id, { callback, delay });
    return id;
  },
  cancel: (id) => timers.delete(id),
});
const settle = () => new Promise((resolve) => setImmediate(resolve));
function tick() {
  const [id, timer] = [...timers][0] || [];
  assert.ok(timer, "a poll was scheduled");
  timers.delete(id);
  timer.callback();
  return timer;
}
feed.setActive(true);
assert.equal(reads, 1);
await feed.refresh();
await feed.refresh();
assert.equal(reads, 1, "a slow GET never overlaps another GET");
pending
  .shift()
  .resolve([{ batch_id: "batch", status: "queued", completed_count: 0 }]);
await settle();
assert.equal(progress.length, 0);
assert.equal(
  tick().delay,
  0,
  "a refresh during a pending read runs once after it finishes",
);
assert.equal(reads, 2);
pending
  .shift()
  .resolve([{ batch_id: "batch", status: "running", completed_count: 1 }]);
await settle();
assert.equal(progress.length, 1);
assert.equal(tick().delay, 2000);
pending
  .shift()
  .resolve([{ batch_id: "batch", status: "running", completed_count: 1 }]);
await settle();
assert.equal(
  progress.length,
  1,
  "identical progress does not repeatedly reload results",
);
tick();
const hiddenRequest = pending.shift();
feed.setActive(false);
assert.equal(hiddenRequest.signal.aborted, true);
hiddenRequest.resolve([
  { batch_id: "batch", status: "succeeded", completed_count: 3 },
]);
await settle();
assert.equal(observed.length, 3, "an aborted hidden-page response is ignored");
assert.equal(timers.size, 0);
await feed.refresh();
assert.equal(reads, 4, "hidden routes cannot fetch");
feed.setActive(true);
assert.equal(reads, 5);
pending
  .shift()
  .resolve([{ batch_id: "batch", status: "succeeded", completed_count: 3 }]);
await settle();
assert.equal(progress.length, 2);
assert.equal(timers.size, 0, "terminal batches stop polling");
const stale = feed.refresh();
pending
  .shift()
  .resolve([{ batch_id: "batch", status: "succeeded", completed_count: 2 }]);
await stale;
assert.equal(
  progress.length,
  2,
  "an older read cannot lower the completion watermark",
);
const failure = feed.refresh();
pending.shift().reject(new Error("fixture read failed"));
await failure;
assert.equal(errors.at(-1), "fixture read failed");
assert.equal(timers.size, 1);
feed.dispose();
assert.equal(timers.size, 0);
assert.equal(maxInFlight, 1);

// Render the actual shared toolbar. SSR must only present state, never enqueue.
const { createElement } = await import("react"),
  { renderToStaticMarkup } = await import("react-dom/server");
let posts = 0;
globalThis.__batchPost = () => {
  posts++;
  throw new Error("render must not POST");
};
const cache = new Map();
const encoded = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
async function moduleUrl(path) {
  if (cache.has(path.href)) return cache.get(path.href);
  let text = ts.transpileModule(await fs.readFile(path, "utf8"), {
    fileName: path.pathname,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [
    ...text.matchAll(/(?:from |import )(["'])([^"']+)\1/g),
  ]) {
    const name = match[2];
    let target;
    if (name.endsWith(".css")) {
      text = text.replace(match[0], "void " + JSON.stringify(name));
      continue;
    }
    if (name.endsWith("/UI"))
      target = encoded(
        "export const ErrorBox=()=>null;export const Spinner=()=>null;",
      );
    else if (name.endsWith("/taskActivity"))
      target = encoded(
        "export const finishTaskRequest=()=>{};export const refreshTaskActivity=()=>{};export const startTaskRequest=()=>{};",
      );
    else if (name.endsWith("/api"))
      target = encoded(
        "export const api=()=>{};export const post=()=>globalThis.__batchPost();export const list=value=>Array.isArray(value)?value:value?.items||[];export const requestKey=()=>'';",
      );
    else if (name.startsWith(".")) {
      const local = new URL(name + ".ts", path);
      try {
        await fs.access(local);
      } catch {
        local.pathname += "x";
      }
      target = await moduleUrl(local);
    } else target = import.meta.resolve(name);
    text = text.replace(
      match[0],
      match[0].replace(match[1] + name + match[1], JSON.stringify(target)),
    );
  }
  const url = encoded(text);
  cache.set(path.href, url);
  return url;
}
const { default: Toolbar } = await import(
  await moduleUrl(new URL("./StrategyBatchToolbar.tsx", import.meta.url))
);
for (const target of ["stock", "sector"]) {
  const html = renderToStaticMarkup(
    createElement(Toolbar, {
      target,
      strategies: [
        { strategy_id: "one" },
        { strategy_id: "two" },
        { strategy_id: "archived", archived: true },
      ],
      selection: new Set(["two"]),
      onSelectAll: () => {},
      onUpdated: () => {},
      onOpenRun: () => {},
      onBusyChange: () => {},
    }),
  );
  assert.match(html, /全选当前分类/);
  assert.match(html, /执行所选并跟踪（1）/);
  assert.match(html, /一键执行并跟踪（2）/);
  assert.match(html, /全部命中自动加入各自策略跟踪池/);
  assert.doesNotMatch(html, /<button[^>]*>(?:(?!<\/button>)[\s\S])*<input/);
}
const guardedHtml = renderToStaticMarkup(createElement(Toolbar, {
  target: "stock",
  strategies: [
    { strategy_id: "done", run_availability: { state: "completed", can_run: false } },
    { strategy_id: "ready", run_availability: { state: "ready", can_run: true } },
    { strategy_id: "missing", run_availability: { state: "unavailable", can_run: false } },
  ],
  selection: new Set(["done", "ready", "missing"]),
  onSelectAll: () => {}, onUpdated: () => {}, onOpenRun: () => {}, onBusyChange: () => {},
}));
assert.match(guardedHtml, /执行所选并跟踪（2）/);
assert.match(guardedHtml, /一键执行并跟踪（2）/);
assert.match(guardedHtml, /个策略已有今日结果，点击直接读取/);
const savedOnlyHtml = renderToStaticMarkup(createElement(Toolbar, {
  target: "stock", strategies: [{ strategy_id: "done", run_availability: { state: "completed", can_run: false } }],
  selection: new Set(["done"]), onSelectAll: () => {}, onUpdated: () => {}, onOpenRun: () => {}, onBusyChange: () => {},
}));
assert.match(savedOnlyHtml, /查看所选今日结果（1）/);
assert.match(savedOnlyHtml, /查看全部今日结果（1）/);
assert.equal(batchTrackingSummary({ receipt_only: true, auto_add: false }).label, "今日已保存结果");
assert.match(batchItemTrackingText({ result_reused: true }, false), /未重新计算或自动入池/);
assert.equal(posts, 0);
console.log(
  "Strategy batches: idempotent uncertain retries, isolated selection, serial/hidden-safe polling, terminal cleanup, saved-progress watermarks and shared stock/sector toolbar passed",
);
