import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

// Execute actual component handlers. API data is an isolated fixture; this test
// never sends requests or modifies a real topic, strategy or membership.
let state = [],
  cursor = 0,
  effects = [],
  dirty = false,
  timers = [];
const requests = [];
const data = {
  items: Array.from({ length: 32 }, (_, index) => ({
    instrument_id: `603${String(index).padStart(3, "0")}.SH`,
    name: `测试成员${index}`,
  })),
  total: 32,
  business_date: "2026-10-27",
  members_complete: true,
};
const uiNames = [
  "Badge",
  "Empty",
  "ErrorBox",
  "Loading",
  "Panel",
  "Table",
  "Warnings",
];
const components = Object.fromEntries(
  uiNames.map((name) => [name, function Component() {}]),
);
globalThis.__topicBoardHooks = {
  components,
  useState(initial) {
    const index = cursor++;
    if (!(index in state)) state[index] = initial;
    return [
      state[index],
      (next) => {
        const value = typeof next === "function" ? next(state[index]) : next;
        if (!Object.is(value, state[index])) {
          state[index] = value;
          dirty = true;
        }
      },
    ];
  },
  useEffect(effect, deps) {
    const index = cursor++;
    if (
      !state[index] ||
      deps.some((value, i) => !Object.is(value, state[index][i]))
    ) {
      state[index] = deps;
      effects.push(effect);
    }
  },
  useApi(path) {
    requests.push(path);
    return { data, error: "", loading: false, reload() {} };
  },
};
globalThis.window = {
  setTimeout(callback) {
    timers.push(callback);
    return timers.length;
  },
  clearTimeout() {},
};
const encoded = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)]) {
    const name = match[2];
    let target;
    if (name === "react")
      target = encoded(
        "export const {useState,useEffect}=globalThis.__topicBoardHooks;",
      );
    else if (name.endsWith("/lib/api"))
      target = encoded(
        "export const useApi=globalThis.__topicBoardHooks.useApi; export const list=data=>data?.items||[];",
      );
    else if (name.endsWith("/UI"))
      target = encoded(
        uiNames
          .map(
            (key) =>
              `export const ${key}=globalThis.__topicBoardHooks.components.${key};`,
          )
          .join("\n") + "export const useToast=()=>()=>{};",
      );
    else if (name.endsWith("/lib/format"))
      target = encoded(
        "export const amount=String,num=String,percent=String,tone=()=>'';",
      );
    else if (
      name.endsWith("/AssociationDialog") ||
      name.endsWith("/WatchlistButton")
    )
      target = encoded("export default function Stub(){};");
    else if (name.endsWith("/TopicDirectory"))
      target = encoded("export const topicKindLabel=()=>'';");
    else if (name.endsWith("/MemberHistoryCells"))
      target = encoded(
        "export function HistoryReturnCell(){} export function MemberMetricCoverage(){} export function RecentLimitCell(){} export function YearLimitCountCell(){}",
      );
    else
      target = name.startsWith(".")
        ? await moduleUrl(new URL(name + ".tsx", path))
        : import.meta.resolve(name);
    source = source.replace(match[0], "from " + JSON.stringify(target));
  }
  const url = encoded(source);
  modules.set(path.href, url);
  return url;
}
const { default: Filter } = await import(
  await moduleUrl(new URL("./TopicBoardFilter.tsx", import.meta.url))
);
const { default: Browser } = await import(
  await moduleUrl(new URL("./TopicBrowser.tsx", import.meta.url))
);
const props = {
  topicIds: ["topic-b", "topic-a"],
  catalog: [
    { topic_id: "topic-a", name: "分类甲" },
    { topic_id: "topic-b", name: "分类乙" },
  ],
  refreshVersion: 0,
  businessDate: "2026-10-27",
  busy: "",
  onRemoveTopic() {},
  onClearTopics() {},
  onMembership() {},
  onStock() {},
  onChanged() {},
};
function render() {
  let tree;
  do {
    dirty = false;
    cursor = 0;
    effects = [];
    tree = Browser(props);
    effects.forEach((effect) => effect());
  } while (dirty);
  return tree;
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...nodes(tree.props?.children)];
}
const find = (type) => nodes(render()).find((node) => node.type === type);
const params = () =>
  new URL(requests.at(-1), "http://fixture.invalid").searchParams;
function clickBoard(label) {
  const control = find(Filter);
  const button = nodes(Filter(control.props)).find(
    (node) => node.type === "button" && node.props.children === label,
  );
  assert.ok(button, `board control ${label} is rendered`);
  button.props.onClick();
  render();
}

render();
assert.deepEqual(params().getAll("boards"), []);
assert.deepEqual(params().getAll("topic_ids"), ["topic-a", "topic-b"]);
assert.equal(find(components.Table).props.pageSize, 25);
assert.equal(
  find(components.Table).props.rows.length,
  32,
  "complete server ordering reaches client pagination",
);
const initialTableKey = find(components.Table).key;
find(components.Table).props.onSelect(data.items[0].instrument_id);
clickBoard("主板");
assert.deepEqual(params().getAll("boards"), ["main"]);
assert.notEqual(
  find(components.Table).key,
  initialTableKey,
  "board change resets pagination",
);
assert.equal(
  find(components.Table).props.selection.size,
  1,
  "explicit cross-filter stock selection is retained",
);
clickBoard("创业板");
assert.deepEqual(params().getAll("boards"), ["main", "chinext"]);
clickBoard("主板");
assert.deepEqual(params().getAll("boards"), ["chinext"]);
clickBoard("科创板");
clickBoard("北交所");
assert.deepEqual(params().getAll("boards"), ["chinext", "star", "beijing"]);
const intersection = nodes(render()).find(
  (node) =>
    node.type === "button" && node.props.children === "同时满足（交集）",
);
intersection.props.onClick();
render();
assert.equal(params().get("match"), "all");
const search = nodes(render()).find(
  (node) => node.props?.["aria-label"] === "搜索联合成员股票",
);
search.props.onChange({ target: { value: "  测试成员  " } });
render();
timers.splice(0).forEach((callback) => callback());
render();
assert.equal(params().get("query"), "测试成员");
assert.deepEqual(params().getAll("boards"), ["chinext", "star", "beijing"]);
const column = find(components.Table).props.columns.find(
  (item) => item.key === "change_30d_pct",
);
column.title.props.onClick();
render();
assert.equal(params().get("sort_by"), "change_30d_pct");
assert.equal(
  params().has("date"),
  false,
  "topic boards do not adopt the market view date",
);
clickBoard("不限");
assert.deepEqual(params().getAll("boards"), []);
assert.equal(params().get("query"), "测试成员");
const unrestricted = nodes(Filter(find(Filter).props)).find(
  (node) => node.type === "button" && node.props.children === "不限",
);
assert.equal(unrestricted.props["aria-pressed"], true);
delete globalThis.__topicBoardHooks;
delete globalThis.window;
console.log(
  "topic listing boards: canonical multi-select toggles/clear, combined server query, pagination reset, full sorted rows, current date and selection scope passed",
);
