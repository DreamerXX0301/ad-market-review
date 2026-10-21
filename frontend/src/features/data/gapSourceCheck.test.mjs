/** Actual React controller with mocked hooks and transport; never contacts a source. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
let source = ts.transpileModule(
  await fs.readFile(new URL("./GapSourceCheck.tsx", import.meta.url), "utf8"),
  {
    fileName: "GapSourceCheck.tsx",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
).outputText;
const slots = [];
let cursor = 0,
  dirty = false,
  effects = [],
  keys = 0,
  changes = 0;
const same = (a, b) =>
  a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
globalThis.__gapHooks = {
  useState(initial) {
    const i = cursor++;
    if (!slots[i]) slots[i] = { value: initial };
    return [
      slots[i].value,
      (next) => {
        const value = typeof next === "function" ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) {
          slots[i].value = value;
          dirty = true;
        }
      },
    ];
  },
  useRef(value) {
    const i = cursor++;
    if (!slots[i]) slots[i] = { value: { current: value } };
    return slots[i].value;
  },
  useEffect(callback, deps) {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps)) {
      const old = slots[i];
      slots[i] = { deps, cleanup: old?.cleanup };
      effects.push(() => {
        slots[i].cleanup?.();
        slots[i].cleanup = callback();
      });
    }
  },
};
const windowSurface = new EventTarget(),
  documentSurface = Object.assign(new EventTarget(), { hidden: false });
globalThis.window = windowSurface;
globalThis.document = documentSurface;
globalThis.location = { hash: "#market" };
const paths = [],
  posts = [],
  waiting = [],
  opened = [];
let reloads = 0;
globalThis.__gapMetadata = {
  available: false,
  plan_id: "plan-1",
  diagnosis_id: "diag-1",
  snapshot_id: "head",
  stock_count: 1125,
  missing_sessions: 7932,
  is_current: false,
  stale: false,
  evidence: null,
};
globalThis.__gapActivity = { tasks: [], pending: [] };
globalThis.__gapJobs = {};
globalThis.__gapAPI = {
  useApi(path) {
    paths.push(path);
    return {
      data:
        path === "/data/quote-gap-source"
          ? globalThis.__gapMetadata
          : path?.startsWith("/tasks/")
            ? globalThis.__gapJobs[path.slice(7)] || null
            : null,
      loading: false,
      error: "",
      reload: () => {
        reloads++;
      },
    };
  },
  requestKey: () => `source-request-${++keys}`,
  post(path, body) {
    posts.push({ path, body });
    return new Promise((resolve, reject) => waiting.push({ resolve, reject }));
  },
};
// Reload callbacks must match the actual useApi stable callback contract.
const stableReload = () => {
  reloads++;
};
const originalUseApi = globalThis.__gapAPI.useApi;
globalThis.__gapAPI.useApi = (path) => ({
  ...originalUseApi(path),
  reload: stableReload,
});
for (const [name, target] of Object.entries({
  react: encode(
    "export const {useEffect,useRef,useState}=globalThis.__gapHooks;",
  ),
  "react/jsx-runtime": encode(
    "export const jsx=(type,props,key)=>({type,props,key});export const jsxs=jsx;export const Fragment='fragment';",
  ),
  "lucide-react": encode(
    "export const CloudDownload=()=>null;export const RefreshCw=()=>null;",
  ),
  "../../lib/api": encode(
    "export const {post,requestKey,useApi}=globalThis.__gapAPI;",
  ),
  "../../lib/format": encode(
    "export const dateTime=(v)=>String(v);export const integer=(v)=>v==null?'—':String(v);",
  ),
  "../../components/UI": encode(
    "export const ErrorBox=()=>null;export const Spinner=()=>null;",
  ),
  "../../components/taskActivity": encode(
    "export const useTaskActivity=()=>globalThis.__gapActivity;export const startTaskRequest=()=>{};export const finishTaskRequest=()=>{};",
  ),
  "../../components/taskActivityPresentation": encode(
    "export const activityPresentation=(t)=>({statusLabel:t.status,stage:'阶段待回报',progressLabel:'50%',countLabel:''});",
  ),
}))
  source = source.replaceAll(
    `from ${JSON.stringify(name)}`,
    `from ${JSON.stringify(target)}`,
  );
const { default: SourceCheck } = await import(encode(source));
const props = {
  disabled: false,
  remoteConfigured: true,
  onTask: (task) => opened.push(task),
  onChanged: () => {
    changes++;
  },
};
let tree;
function render() {
  for (let pass = 0; pass < 20; pass++) {
    cursor = 0;
    dirty = false;
    effects = [];
    tree = SourceCheck(props);
    for (const effect of effects) effect();
    if (!dirty) return tree;
  }
  throw Error("controller render loop");
}
function nodes(value) {
  if (value == null || typeof value !== "object") return [];
  return [
    value,
    ...(Array.isArray(value.props?.children)
      ? value.props.children
      : [value.props?.children]
    ).flatMap(nodes),
  ];
}
const main = () =>
  nodes(tree).find(
    (node) =>
      node.type === "button" && node.props.className?.includes("primary"),
  );
const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  render();
};
render();
assert.equal(posts.length, 0);
assert.deepEqual(paths.slice(-2), [null, null]);
location.hash = "#data";
windowSurface.dispatchEvent(new Event("hashchange"));
render();
assert.ok(paths.includes("/data/quote-gap-source"));
assert.equal(main().props.disabled, true);
const beforePlan = reloads;
globalThis.__gapMetadata = { ...globalThis.__gapMetadata, available: true };
props.planKey = "checked-local-plan";
render();
assert.ok(
  reloads > beforePlan,
  "local coverage completion changing the plan refreshes source availability without remount",
);
assert.equal(
  posts.length,
  0,
  "refreshing availability never creates a source task",
);
assert.equal(main().props.disabled, false);
main().props.onClick();
main().props.onClick();
render();
assert.equal(posts.length, 1, "double click submits one explicit source task");
assert.equal(main().props.disabled, true);
waiting.shift().reject(Error("uncertain response"));
await flush();
const firstKey = posts[0].body.request_key;
main().props.onClick();
render();
assert.equal(
  posts[1].body.request_key,
  firstKey,
  "uncertain response retries preserve the request key",
);
assert.deepEqual(
  Object.keys(posts[1].body).sort(),
  ["plan_id", "request_key"],
  "all stocks are frozen by server plan, not guessed or silently narrowed",
);
waiting.shift().resolve({
  job_id: "source-job",
  status: "queued",
  parameters: { operation: "quote_gap_source_check" },
});
await flush();
assert.equal(opened.length, 1);
assert.equal(
  main().props.disabled,
  true,
  "POST success remains busy before the compact feed catches up",
);
globalThis.__gapActivity = {
  tasks: [
    {
      job_id: "source-job",
      operation: "quote_gap_source_check",
      status: "succeeded",
      finished_at_ms: 111,
    },
  ],
  pending: [],
};
render();
assert.equal(changes, 1);
assert.equal(main().props.disabled, false);
render();
assert.equal(changes, 1, "same finished receipt cannot trigger a reload loop");
main().props.onClick();
render();
assert.notEqual(
  posts.at(-1).body.request_key,
  firstKey,
  "an intentional later check gets a new key",
);
waiting.shift().reject(Error("uncertain second attempt"));
await flush();
const secondKey = posts.at(-1).body.request_key;
globalThis.__gapMetadata = { ...globalThis.__gapMetadata, plan_id: "plan-2" };
render();
main().props.onClick();
render();
assert.notEqual(
  posts.at(-1).body.request_key,
  secondKey,
  "changed immutable plan never reuses an old key",
);
waiting.shift().resolve({
  job_id: "source-job-2",
  status: "queued",
  parameters: { operation: "quote_gap_source_check" },
});
await flush();
globalThis.__gapActivity = { tasks: [], pending: [] };
globalThis.__gapJobs["source-job-2"] = {
  job_id: "source-job-2",
  status: "succeeded",
  parameters: { operation: "quote_gap_source_check" },
  finished_at_ms: 222,
};
render();
assert.equal(
  main().props.disabled,
  false,
  "task aged out of compact history is recovered by exact job ID",
);
globalThis.__gapMetadata = {
  ...globalThis.__gapMetadata,
  evidence: {
    planned_stocks: 3,
    checked_stocks: 3,
    failed_stocks: 0,
    checked_sessions: 14,
    total_gap_sessions: 7932,
    gap_stock_count: 1125,
    fillable_sessions: 0,
    source_absent_sessions: 14,
    overlap_difference_sessions: 0,
    checked_at_ms: 123,
  },
};
render();
function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  return text(node.props?.children);
}
const subsetText = text(tree);
assert.match(subsetText, /本次股票核验3 \/ 3/);
assert.match(subsetText, /已核验缺口14 \/ 7932/);
assert.match(subsetText, /全部缺口涉及 1125 只股票；本次计划 3 只/);
assert.doesNotMatch(
  subsetText,
  /100%/,
  "a completed three-stock subset cannot claim complete all-market verification",
);
location.hash = "#watchlist";
windowSurface.dispatchEvent(new Event("hashchange"));
render();
assert.deepEqual(paths.slice(-2), [null, null]);
assert.ok(
  posts.every((item) => item.path === "/data/quote-gap-source"),
  "all POSTs are explicit checks; no sync, quote repair or publication is created",
);
for (const slot of slots) slot?.cleanup?.();
console.log(
  "Gap source check: GET-only mount, hidden stop, explicit full-plan submission, double-click guard, idempotent uncertainty and durable completion refresh passed",
);
