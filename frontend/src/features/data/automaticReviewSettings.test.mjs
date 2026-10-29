/** Actual settings controller with hooks/transport fixtures; never starts a task. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const original = await fs.readFile(
  new URL("./AutomaticReviewSettings.tsx", import.meta.url),
  "utf8",
);
let source = ts.transpileModule(original, {
  fileName: "AutomaticReviewSettings.tsx",
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;
const helper = ts.transpileModule(
  await fs.readFile(
    new URL("./automaticReviewPresentation.ts", import.meta.url),
    "utf8",
  ),
  { compilerOptions: { module: ts.ModuleKind.ES2022 } },
).outputText;
const slots = [];
let cursor = 0,
  dirty = false,
  effects = [];
const same = (a, b) =>
  a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
globalThis.__automaticHooks = {
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
let active = true,
  reloads = 0;
const requests = [],
  waiting = [],
  paths = [],
  opened = [];
const result = {
  data: null,
  error: "",
  loading: false,
  setData(value) {
    result.data = value;
    dirty = true;
  },
  reload() {
    reloads++;
  },
};
globalThis.__automaticAPI = {
  usePageActive: () => active,
  useApi(path) {
    paths.push(path);
    return result;
  },
  api(path, options) {
    requests.push({ path, options, body: JSON.parse(options.body) });
    return new Promise((resolve, reject) => waiting.push({ resolve, reject }));
  },
};
for (const [name, replacement] of Object.entries({
  react: encode(
    "export const {useEffect,useRef,useState}=globalThis.__automaticHooks;",
  ),
  "react/jsx-runtime": encode(
    "export const jsx=(type,props,key)=>({type,props,key});export const jsxs=jsx;export const Fragment='fragment';",
  ),
  "lucide-react": encode(
    "export const Clock3=()=>null;export const RefreshCw=()=>null;export const Save=()=>null;",
  ),
  "../../lib/api": encode(
    "export const {api,useApi,usePageActive}=globalThis.__automaticAPI;",
  ),
  "../../lib/format": encode("export const dateTime=(v)=>String(v);"),
  "../../components/UI": encode(
    "export const Badge='Badge',ErrorBox='ErrorBox',Panel='Panel',Spinner='Spinner',Status='Status';",
  ),
  "./automaticReviewPresentation": encode(helper),
}))
  source = source.replaceAll(
    `from ${JSON.stringify(name)}`,
    `from ${JSON.stringify(replacement)}`,
  );
source = source.replace('import "./AutomaticReviewSettings.css";', "");
const { default: Settings } = await import(encode(source));
let tree;
function render() {
  for (let pass = 0; pass < 20; pass++) {
    cursor = 0;
    dirty = false;
    effects = [];
    tree = Settings({ onSelectTask: (id) => opened.push(id) });
    for (const effect of effects) effect();
    if (!dirty) return tree;
  }
  throw Error("controller render loop");
}
function nodes(value) {
  if (Array.isArray(value)) return value.flatMap(nodes);
  if (!value || typeof value !== "object") return [];
  return [value, ...nodes(value.props?.children)];
}
function text(value = tree) {
  if (Array.isArray(value))
    return value.map((item) => text(item ?? null)).join("");
  if (value == null || typeof value === "boolean") return "";
  if (typeof value !== "object") return String(value);
  return text(value.props?.children ?? null);
}
const button = (label) =>
  nodes(tree).find(
    (node) => node.type === "button" && text(node).includes(label),
  );
const toggles = () =>
  nodes(tree).filter((node) => node.props?.type === "checkbox");
const times = () => nodes(tree).filter((node) => node.props?.type === "time");
const errors = () => nodes(tree).filter((node) => node.type === "ErrorBox");
const setToggle = (index, enabled) => {
  toggles()[index].props.onChange({ target: { checked: enabled } });
  render();
};
const setTime = (index, time) => {
  times()[index].props.onChange({ target: { value: time } });
  render();
};
const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  render();
};
const base = {
  timezone: "Asia/Shanghai",
  revision: 0,
  noon: { enabled: false, time: "12:05" },
  after_close: { enabled: false, time: "18:30" },
  last_checks: { noon: null, after_close: null },
  auction: {
    enabled: false,
    revision: 0,
    mode: "off",
    fixed_time: "09:25:05",
    window_end: "09:30:00",
    can_enable: true,
    legacy_configuration: false,
    legacy_instrument_count: 0,
  },
};

render();
assert.equal(requests.length, 0);
assert.ok(paths.every((path) => path === "/settings/automatic-tasks"));
assert.equal(
  toggles().every((node) => node.props.disabled),
  true,
);
assert.equal(button("保存自动设置").props.disabled, true);
assert.equal(tree.props.actions.props.children, "设置待读取");
assert.ok(
  errors().length,
  "unread settings never pretend to be a saved off plan",
);
result.loading = true;
render();
assert.match(text(), /正在读取自动复盘设置/);
result.loading = false;
result.error = "暂不可读取";
render();
assert.equal(errors()[0].props.message, "暂不可读取");
result.data = { ...base, noon: { enabled: "false", time: "12:05" } };
render();
assert.equal(button("保存自动设置").props.disabled, true);
assert.equal(tree.props.actions.props.children, "设置待读取");

result.data = base;
result.error = "";
render();
assert.equal(tree.props.actions.props.children, "自动未开启");
assert.deepEqual(
  toggles().map((node) => node.props.checked),
  [false, false, false],
);
assert.deepEqual(
  times().map((node) => node.props.value),
  ["12:05", "18:30"],
);
assert.equal(button("保存自动设置").props.disabled, true);
assert.match(text(), /暂无调度检查记录，不代表当天已经执行/);
assert.match(text(), /盘后完成需确认结果才解锁/);
// New defaults are server data; already saved schedules must remain untouched.
result.data = {
  ...base,
  after_close: { enabled: false, time: "17:00" },
  auction: {
    ...base.auction,
    can_enable: false,
    reason: "唯一worker未运行，请检查初始化状态",
    worker: { alive: false },
  },
};
render();
assert.equal(times()[1].props.value, "17:00");
assert.match(text(), /默认17:00/);
assert.equal(
  toggles().length,
  2,
  "ineligible auction has a status, not an inert checkbox",
);
assert.match(text(), /暂不可开启/);
assert.match(text(), /采集服务未运行，请检查初始化状态/);
assert.match(text(), /服务未运行/);
assert.doesNotMatch(text(), /worker|工作进程|固定时间/);
assert.equal(
  (text().match(/09:25:05/g) || []).length,
  1,
  "auction time is shown once",
);
assert.equal(
  requests.length,
  0,
  "unavailable auction is never enabled by default",
);
result.data = {
  ...base,
  auction: { ...base.auction, enabled: true, mode: "daily", can_enable: false },
};
render();
assert.equal(
  toggles().length,
  3,
  "existing enabled plan retains its off switch",
);
assert.equal(toggles()[0].props.checked, true);
assert.equal(toggles()[0].props.disabled, false);
result.data = { ...base, after_close: { enabled: true, time: "18:00" } };
render();
assert.equal(
  times()[1].props.value,
  "18:00",
  "saved 18:00 schedule is not rewritten",
);
result.data = base;
render();
assert.equal(
  times()[1].props.value,
  "18:30",
  "saved 18:30 schedule is not rewritten by the new default",
);
setToggle(1, true);
assert.equal(
  requests.length,
  0,
  "editing an opt-in is not permission to run a task",
);
assert.equal(
  tree.props.actions.props.children,
  "自动未开启",
  "badge remains saved state",
);
setTime(0, "13:00");
assert.equal(button("保存自动设置").props.disabled, true);
await button("保存自动设置").props.onClick();
assert.equal(requests.length, 0, "invalid time is rejected by controller too");
setTime(0, "12:10");
setToggle(2, true);
setTime(1, "17:00");
setToggle(0, true);
const save = button("保存自动设置").props.onClick;
save();
save();
render();
assert.equal(requests.length, 1, "double click performs one settings PUT");
assert.deepEqual(requests[0], {
  path: "/settings/automatic-tasks",
  options: {
    method: "PUT",
    body: JSON.stringify({
      expected_revision: 0,
      expected_auction_revision: 0,
      noon: { enabled: true, time: "12:10" },
      after_close: { enabled: true, time: "17:00" },
      auction: { enabled: true },
    }),
  },
  body: {
    expected_revision: 0,
    expected_auction_revision: 0,
    noon: { enabled: true, time: "12:10" },
    after_close: { enabled: true, time: "17:00" },
    auction: { enabled: true },
  },
});
assert.equal(
  toggles().every((node) => node.props.disabled),
  true,
);
assert.equal(
  times().every((node) => node.props.disabled),
  true,
);
assert.equal(button("重新读取").props.disabled, true);
waiting.shift().resolve({
  ...base,
  revision: 1,
  noon: { enabled: true, time: "12:10" },
  after_close: { enabled: true, time: "17:00" },
  auction: { ...base.auction, enabled: true, revision: 1, mode: "daily" },
});
await flush();
assert.equal(tree.props.actions.props.children, "自动已开启");
assert.equal(button("保存自动设置").props.disabled, true);
assert.match(text(), /保存计划不代表任务已经创建或执行/);
assert.equal(
  requests.length,
  1,
  "save success does not create a follow-up task",
);

setTime(0, "12:11");
button("保存自动设置").props.onClick();
waiting.shift().reject(Object.assign(Error("设置修订已变化"), { status: 409 }));
await flush();
assert.equal(
  times()[0].props.value,
  "12:11",
  "revision conflict preserves the user's draft",
);
assert.match(errors().at(-1).props.message, /重新读取最新设置后再保存/);
assert.equal(requests.at(-1).body.expected_revision, 1);
const beforeReload = reloads;
button("放弃修改并重新读取").props.onClick();
render();
assert.equal(reloads, beforeReload + 1);
assert.equal(result.data, null);
assert.equal(times()[0].props.value, "");
assert.equal(button("保存自动设置").props.disabled, true);
result.data = {
  ...base,
  revision: 4,
  last_checks: {
    noon: {
      reason: "同日已有午评任务",
      date: "2026-10-29",
      checked_at: "2026-10-29T12:06:00+08:00",
      task_id: "existing-noon",
      task_status: "succeeded",
    },
    after_close: null,
  },
};
render();
assert.match(text(), /同日已有午评任务/);
button("查看已有任务").props.onClick();
assert.deepEqual(opened, ["existing-noon"]);
assert.equal(requests.length, 2);
setToggle(2, true);
active = false;
render();
await button("保存自动设置").props.onClick();
assert.equal(requests.length, 2, "hidden retained page cannot save settings");
active = true;
render();
button("保存自动设置").props.onClick();
assert.equal(
  requests.at(-1).body.expected_revision,
  4,
  "reload adopts current revision",
);
waiting
  .shift()
  .resolve({ ...base, revision: 5, after_close: { enabled: true } });
await flush();
assert.match(errors().at(-1).props.message, /保存响应不完整/);
assert.equal(
  nodes(tree).some((node) => node.props?.role === "status"),
  false,
);
assert.equal(
  times()[1].props.value,
  "18:30",
  "malformed response does not clear the draft",
);
assert.ok(
  requests.every(
    (request) =>
      request.options.method === "PUT" &&
      request.path === "/settings/automatic-tasks",
  ),
);
assert.equal(
  /setInterval|setTimeout|AudioContext|localStorage|Notification|\/sync\/increment|\/market\/noon/.test(
    original,
  ),
  false,
  "settings card has no scheduler, audio, browser permission or task creation endpoint",
);
button("放弃修改并重新读取").props.onClick();
render();
result.data = { ...base, auction: undefined };
render();
assert.equal(
  toggles().every((n) => n.props.disabled),
  true,
  "missing one plan disables all edits",
);
assert.equal(button("保存自动设置").props.disabled, true);
result.data = base;
render();
setToggle(0, true);
result.data = { ...base, auction: { ...base.auction, revision: 1 } };
render();
assert.equal(
  button("保存自动设置").props.disabled,
  true,
  "auction revision refresh invalidates an old draft",
);
const beforeStale = requests.length;
await button("保存自动设置").props.onClick();
assert.equal(requests.length, beforeStale);
button("放弃修改并重新读取").props.onClick();
render();
result.data = base;
render();
setToggle(1, true);
result.error = "settings unavailable";
render();
assert.equal(button("保存自动设置").props.disabled, true);
await button("保存自动设置").props.onClick();
assert.equal(
  requests.length,
  beforeStale,
  "cached data with GET error cannot save",
);
result.error = "";
render();
button("保存自动设置").props.onClick();
waiting.shift().resolve({ ...base, revision: 1 });
await flush();
assert.match(errors().at(-1).props.message, /与本次修改不一致/);
assert.equal(
  button("保存自动设置").props.disabled,
  true,
  "unconfirmed response requires reload before retry",
);
assert.equal(times()[1].props.min, "17:00");
assert.equal(
  nodes(tree).filter((n) => n.props?.type === "time").length,
  2,
  "auction has no editable time",
);
assert.match(text(), /北京时间.*09:25:05/);
assert.doesNotMatch(text(), /固定时间|worker|工作进程/);
assert.equal(original.includes("#data?section=automatic"), true);
console.log(
  "Automatic settings controller: unknown/opt-in/ranges/double-save/revisions/conflict/reload/inactive/existing task/invalid response/no implicit task execution passed",
);
