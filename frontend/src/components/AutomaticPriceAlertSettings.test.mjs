import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const compile = (text, fileName = "test.ts") =>
  ts.transpileModule(text, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const presentationUrl = encode(
  compile(
    await fs.readFile(
      new URL("./automaticPriceAlertPresentation.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const helpers = await import(presentationUrl);
const instrumentId = "000678.SZ";
const fixture = () => ({
  instrument_id: instrumentId,
  rule: {
    rule_id: null,
    revision: 0,
    enabled: false,
    modes: [],
    require_volume: true,
    algorithm: "structure-volume-levels-v1",
    effective_after_date: null,
  },
  latest_evaluation: null,
  latest_baseline: null,
});
assert.equal(helpers.automaticAlertConfig(fixture(), "600519.SH"), null);
assert.equal(
  helpers.automaticAlertConfig(
    { ...fixture(), rule: { ...fixture().rule, modes: ["unknown"] } },
    instrumentId,
  ),
  null,
);
assert.equal(
  helpers.automaticAlertConfig(
    { ...fixture(), rule: { ...fixture().rule, revision: -1 } },
    instrumentId,
  ),
  null,
);
assert.throws(
  () =>
    helpers.automaticAlertPayload(
      { enabled: true, modes: [], require_volume: true },
      fixture(),
    ),
  /至少选择/,
);

let states = [],
  refs = [],
  effects = [],
  queued = [],
  cursor = 0,
  updates = 0;
const same = (a, b) =>
  a?.length === b?.length &&
  a.every((value, index) => Object.is(value, b[index]));
const calls = [],
  paths = [],
  events = [];
let response = fixture(),
  resultData = fixture(),
  resultLoading = false;
const result = {
  get data() {
    return resultData;
  },
  get loading() {
    return resultLoading;
  },
  error: "",
  reload: () => calls.push(["reload"]),
};
globalThis.window = new EventTarget();
window.location = { hash: "" };
window.addEventListener("workbench-automatic-price-alerts-changed", (event) =>
  events.push(event.detail),
);
globalThis.__automaticPriceHooks = {
  useState(initial) {
    const at = cursor++;
    if (!(at in states))
      states[at] = typeof initial === "function" ? initial() : initial;
    return [
      states[at],
      (value) => {
        updates++;
        states[at] = typeof value === "function" ? value(states[at]) : value;
      },
    ];
  },
  useRef(initial) {
    const at = cursor++;
    return (refs[at] ||= { current: initial });
  },
  useEffect(effect, deps) {
    const at = cursor++;
    if (!effects[at] || !same(effects[at].deps, deps))
      queued.push(() => {
        effects[at]?.cleanup?.();
        effects[at] = { deps, cleanup: effect() };
      });
  },
  useApi(path) {
    paths.push(path);
    return result;
  },
  async api(path, options) {
    calls.push([path, options]);
    return typeof response === "function" ? await response() : response;
  },
  notifyReadChanges(changes) {
    calls.push(["notify", changes]);
  },
  refreshTaskActivity() {
    calls.push(["tasks"]);
  },
  toast(message) {
    calls.push(["toast", message]);
  },
};
const modules = {
  react: encode(
    "export const {useState,useRef,useEffect}=globalThis.__automaticPriceHooks;",
  ),
  "lucide-react": encode("export const BellRing='bell',Settings2='settings';"),
  "../lib/api": encode(
    "export const {useApi,api,notifyReadChanges}=globalThis.__automaticPriceHooks;",
  ),
  "../lib/workbenchEvents": encode(
    "export const AUTOMATIC_PRICE_ALERTS_CHANGED='workbench-automatic-price-alerts-changed',LINE_ALERT_CHECK_FINISHED='workbench-line-alert-check-finished';",
  ),
  "../features/watchlist/lineAlertNotification": encode(
    "export const LINE_ALERTS_OPEN='workbench-open-today-line-alerts';",
  ),
  "./UI": encode(
    "export const ErrorBox='ErrorBox',Loading='Loading',Modal='Modal'; export const useToast=()=>globalThis.__automaticPriceHooks.toast;",
  ),
  "../lib/format": encode(
    "export const num=value=>value==null?'—':Number(value).toFixed(2);",
  ),
  "./taskActivity": encode(
    "export const {refreshTaskActivity}=globalThis.__automaticPriceHooks;",
  ),
  "./automaticPriceAlertPresentation": presentationUrl,
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
};
let source = compile(
  await fs.readFile(
    new URL("./AutomaticPriceAlertSettings.tsx", import.meta.url),
    "utf8",
  ),
  "AutomaticPriceAlertSettings.tsx",
);
for (const [name, url] of Object.entries(modules))
  source = source.replaceAll(
    `from ${JSON.stringify(name)}`,
    `from ${JSON.stringify(url)}`,
  );
source =
  source.replace('import "./AutomaticPriceAlertSettings.css";', "") +
  "\nexport { AutomaticPriceAlertEditor };";
const { default: Settings, AutomaticPriceAlertEditor: Editor } = await import(
  encode(source)
);
const flatten = (value) =>
  Array.isArray(value)
    ? value.flatMap(flatten)
    : value?.props
      ? [value, ...flatten(value.props.children)]
      : [];
const text = (value) =>
  Array.isArray(value)
    ? value.map(text).join("")
    : value?.props
      ? text(value.props.children)
      : value == null
        ? ""
        : String(value);
const reset = () => {
  effects.forEach((item) => item?.cleanup?.());
  states = [];
  refs = [];
  effects = [];
  queued = [];
  cursor = 0;
  calls.length = 0;
  paths.length = 0;
};
const render = (Component = Editor, id = instrumentId) => {
  cursor = 0;
  const tree = Component({
    instrumentId: id,
    name: "襄阳轴承",
    onClose: () => calls.push(["close-editor"]),
    onViewAlerts: () => calls.push(["close-drawer", window.location.hash]),
  });
  const run = queued;
  queued = [];
  run.forEach((effect) => effect());
  return tree;
};
const inputs = (tree) =>
  flatten(tree).filter(
    (item) => item.type === "input" && item.props.type === "checkbox",
  );
const button = (tree, label) =>
  flatten(tree).find(
    (item) => item.type === "button" && text(item).includes(label),
  );
const tick = () => new Promise((resolve) => setImmediate(resolve));

reset();
let tree = render(Settings);
assert.equal(paths.length, 0, "closed settings must perform no config GET");
button(tree, "提醒设置").props.onClick();
tree = render(Settings);
assert.ok(flatten(tree).some((item) => item.type === Editor));
button(tree, "查看提醒").props.onClick();
assert.deepEqual(
  calls.find((item) => item[0] === "close-drawer"),
  ["close-drawer", ""],
);
assert.equal(window.location.hash, "watchlist?alerts=today");

reset();
resultData = fixture();
render();
tree = render();
assert.equal(paths[0], `/instruments/${instrumentId}/automatic-price-alerts`);
assert.equal(inputs(tree).length, 6);
assert.equal(inputs(tree)[0].props.checked, false);
assert.equal(
  inputs(tree)[5].props.disabled,
  true,
  "volume switch applies only to breakout",
);
assert.equal(
  calls.filter((item) => item[1]?.method).length,
  0,
  "reading settings cannot enqueue evaluation",
);
inputs(tree)[0].props.onChange({ target: { checked: true } });
tree = render();
button(tree, "保存设置").props.onClick();
await tick();
tree = render();
assert.ok(
  flatten(tree).some((item) => item.props.message?.includes("至少选择")),
);
assert.equal(calls.filter((item) => item[1]?.method).length, 0);
inputs(tree)[2].props.onChange({ target: { checked: true } });
tree = render();
assert.equal(inputs(tree)[5].props.disabled, false);
response = {
  ...fixture(),
  rule: {
    ...fixture().rule,
    rule_id: "auto-one",
    revision: 1,
    enabled: true,
    modes: ["close_breakout"],
    effective_after_date: "2026-11-11",
  },
  task: { job_id: "prepare-one", status: "queued" },
};
button(tree, "保存设置").props.onClick();
await tick();
tree = render();
const put = calls.find((item) => item[1]?.method === "PUT");
assert.equal(put[0], `/instruments/${instrumentId}/automatic-price-alerts`);
assert.deepEqual(JSON.parse(put[1].body), {
  enabled: true,
  modes: ["close_breakout"],
  require_volume: true,
  profile: "structure",
  max_risk_pct: 5,
  expected_revision: 0,
});
assert.match(
  calls.find((item) => item[0] === "toast")[1],
  /下一交易日参考位已安排准备/,
);
assert.equal(calls.filter((item) => item[0] === "close-editor").length, 1);
assert.deepEqual(events, [instrumentId]);
assert.ok(
  calls.some(
    (item) => item[0] === "notify" && item[1][0].scope === "price_alerts",
  ),
);
assert.equal(
  calls.some((item) => String(item[0]).includes("/annotations")),
  false,
  "automatic rules never edit manual lines",
);

// A current read must not silently advance an already-edited draft's revision.
const writesBefore = calls.filter((item) => item[1]?.method === "PUT").length;
resultData = {
  ...response,
  rule: { ...response.rule, revision: 2, modes: ["stop_breakdown"] },
};
render();
tree = render();
assert.equal(
  inputs(tree)[2].props.checked,
  true,
  "unsaved draft remains visible",
);
button(tree, "保存设置").props.onClick();
await tick();
tree = render();
assert.equal(
  calls.filter((item) => item[1]?.method === "PUT").length,
  writesBefore,
);
assert.ok(
  flatten(tree).some((item) => item.props.message?.includes("其他位置更新")),
);
flatten(tree)
  .find((item) => item.type === "ErrorBox")
  .props.retry();
resultData = structuredClone(resultData);
render();
tree = render();
assert.equal(
  inputs(tree)[4].props.checked,
  true,
  "explicit reload adopts external config",
);

reset();
resultData = {
  ...fixture(),
  latest_evaluation: {
    state: "prepared",
    target_date: "2026-11-11",
    reason: "不追溯触发",
  },
  latest_baseline: {
    as_of: "2026-11-11",
    algorithm: "structure-volume-levels-v1",
    levels: {
      support: { price: 17.4 },
      stop: { price: 19.67 },
      breakout: { price: 20.73 },
    },
  },
};
render();
tree = render();
assert.match(text(tree), /已准备次日参考位/);
assert.match(text(tree), /已发布个股日K · 未复权原价 · 截至 2026-11-11/);
assert.match(text(tree), /19.67 元/);
assert.ok(
  flatten(tree)
    .filter((item) => item.type === "details")
    .every((item) => !item.props.open),
);

// A late save may finish on the server, but cannot update an unmounted editor.
inputs(tree)[1].props.onChange({ target: { checked: true } });
tree = render();
let resolve;
response = () =>
  new Promise((done) => {
    resolve = done;
  });
button(tree, "保存设置").props.onClick();
effects.forEach((item) => item?.cleanup?.());
const count = updates;
const closesBeforeLateSave = calls.filter(
  (item) => item[0] === "close-editor",
).length;
resolve({ ...fixture(), rule: { ...fixture().rule, revision: 1 } });
await tick();
assert.equal(updates, count);
assert.equal(
  calls.filter((item) => item[0] === "close-editor").length,
  closesBeforeLateSave,
  "a late response cannot close another editor",
);
assert.equal(
  calls.some((item) => item[0] === "toast"),
  false,
);

reset();
resultData = { ...fixture(), instrument_id: "600519.SH" };
render();
tree = render();
assert.equal(
  inputs(tree).length,
  0,
  "foreign stock config never populates form",
);
assert.equal(button(tree, "保存设置").props.disabled, true);

// New short rules are explicit, editable, and never reuse a legacy algorithm.
const shortFixture = () => ({
  ...fixture(),
  rule: {
    ...fixture().rule,
    profile: "short_week_v1",
    max_risk_pct: 5,
    algorithm: "short-week-risk-levels-v1",
  },
});
assert.equal(
  helpers.automaticAlertConfig(fixture(), instrumentId).rule.profile,
  "structure",
);
assert.equal(
  helpers.automaticAlertConfig(shortFixture(), instrumentId).rule.profile,
  "short_week_v1",
);
assert.equal(
  helpers.automaticAlertConfig(
    {
      ...shortFixture(),
      rule: { ...shortFixture().rule, algorithm: "structure-volume-levels-v1" },
    },
    instrumentId,
  ),
  null,
);
for (const risk of [NaN, 0, 20.01, 5.001]) {
  assert.equal(
    helpers.automaticAlertConfig(
      {
        ...shortFixture(),
        rule: { ...shortFixture().rule, max_risk_pct: risk },
      },
      instrumentId,
    ),
    null,
  );
  assert.throws(
    () =>
      helpers.automaticAlertPayload(
        {
          ...helpers.automaticAlertDraft(shortFixture().rule),
          max_risk_pct: risk,
        },
        shortFixture(),
      ),
    /风险距离/,
  );
}
reset();
resultData = shortFixture();
render();
tree = render();
assert.match(text(tree), /只抬高不下调/);
assert.match(text(tree), /短线风险线跌破/);
const select = flatten(tree).find((item) => item.type === "select");
assert.equal(select.props.value, "short_week_v1");
const riskInput = () =>
  flatten(tree).find(
    (item) => item.type === "input" && item.props.type === "number",
  );
assert.equal(riskInput().props.value, 5);
button(tree, "3%").props.onClick();
tree = render();
assert.equal(riskInput().props.value, 3);
riskInput().props.onChange({ target: { value: "4.25" } });
tree = render();
assert.equal(riskInput().props.value, 4.25);
inputs(tree)[0].props.onChange({ target: { checked: true } });
tree = render();
inputs(tree)[4].props.onChange({ target: { checked: true } });
tree = render();
response = {
  ...shortFixture(),
  rule: {
    ...shortFixture().rule,
    enabled: true,
    revision: 1,
    modes: ["stop_breakdown"],
    max_risk_pct: 4.25,
  },
};
button(tree, "保存设置").props.onClick();
await tick();
tree = render();
const shortPut = calls.find((item) => item[1]?.method === "PUT");
assert.deepEqual(JSON.parse(shortPut[1].body), {
  enabled: true,
  modes: ["stop_breakdown"],
  require_volume: true,
  profile: "short_week_v1",
  max_risk_pct: 4.25,
  expected_revision: 0,
});
assert.equal(
  calls.some((item) => String(item[0]).includes("/annotations")),
  false,
);

reset();
resultData = {
  ...shortFixture(),
  rule: {
    ...shortFixture().rule,
    enabled: true,
    revision: 2,
    max_risk_pct: 8,
    modes: ["stop_breakdown"],
  },
  latest_baseline: {
    as_of: "2026-11-11",
    algorithm: "short-week-risk-levels-v1",
    levels: { stop: { price: 19.25, state: "breached" } },
  },
};
render();
tree = render();
assert.match(text(tree), /8.00%/);
assert.match(text(tree), /19.25 元/);
assert.match(text(tree), /已失守/);
assert.match(text(tree), /默认5%上限/);
const resetBaseline = button(tree, "重新设定短线基准");
assert.ok(resetBaseline);
response = {
  ...resultData,
  rule: { ...resultData.rule, revision: 3 },
  latest_baseline: null,
  task: { job_id: "reset-short-baseline", status: "queued" },
};
resetBaseline.props.onClick();
await tick();
tree = render();
assert.equal(
  JSON.parse(calls.find((item) => item[1]?.method === "PUT")[1].body)
    .reset_monitoring,
  true,
);
assert.match(text(tree), /下一交易日参考位已安排准备/);
assert.equal(
  calls.some((item) => item[0] === "close-editor"),
  false,
  "baseline reset stays open so its status remains visible",
);

reset();
resultData = {
  ...shortFixture(),
  rule: {
    ...shortFixture().rule,
    enabled: true,
    revision: 2,
    modes: ["stop_breakdown"],
  },
  latest_baseline: {
    as_of: "2026-11-11",
    levels: { stop: { price: 19.25, state: "breached" } },
  },
};
render();
tree = render();
button(tree, "8%").props.onClick();
tree = render();
button(tree, "重新设定短线基准").props.onClick();
await tick();
tree = render();
assert.equal(calls.filter((item) => item[1]?.method === "PUT").length, 0);
assert.ok(
  flatten(tree).some((item) => item.props.message?.includes("请先保存")),
);

// Failed requests and invalid receipts must preserve the editor and allow retry.
for (const failedResponse of [
  () => {
    throw new Error("保存失败，请稍后重试");
  },
  { ...fixture(), instrument_id: "600519.SH" },
]) {
  reset();
  resultData = fixture();
  response = failedResponse;
  render();
  tree = render();
  button(tree, "启用一周短线").props.onClick();
  await tick();
  tree = render();
  assert.equal(
    calls.some((item) => item[0] === "close-editor"),
    false,
  );
  assert.equal(
    calls.some((item) => item[0] === "toast"),
    false,
  );
  assert.ok(flatten(tree).some((item) => item.type === "ErrorBox"));
  assert.equal(button(tree, "启用一周短线").props.disabled, false);
}
reset();
resultData = shortFixture();
render();
tree = render();
assert.match(text(tree), /一周短线 · 5%/);
assert.match(text(tree), /支撑回踩收回、放量收盘突破、短线风险线跌破/);
assert.equal(button(tree, "保存设置").props.hidden, true);
const quickDraft = {
  enabled: true,
  profile: "short_week_v1",
  max_risk_pct: 5,
  modes: ["support_reclaim", "close_breakout", "stop_breakdown"],
  require_volume: true,
};
response = {
  ...resultData,
  rule: { ...resultData.rule, ...quickDraft, revision: 1 },
  task: { job_id: "quick-prep" },
};
const quickEnable = button(tree, "启用一周短线");
quickEnable.props.onClick();
quickEnable.props.onClick();
await tick();
assert.deepEqual(
  calls
    .filter((item) => item[1]?.method === "PUT")
    .map(([, options]) => JSON.parse(options.body)),
  [{ ...quickDraft, expected_revision: 0 }],
  "the preset makes one revision-checked save and skips baseline reset",
);
assert.equal(calls.filter((item) => item[0] === "close-editor").length, 1);
assert.match(calls.find((item) => item[0] === "toast")[1], /配置已保存/);

reset();
resultData = {
  ...fixture(),
  rule: {
    ...fixture().rule,
    rule_id: "saved",
    revision: 4,
    modes: ["support_breakdown"],
    require_volume: false,
  },
};
render();
tree = render();
assert.equal(button(tree, "启用一周短线"), undefined);
const custom = flatten(tree).find(
  (item) => item.props.className === "automatic-price-alert-custom",
);
custom.props.onToggle({ currentTarget: { open: true } });
tree = render();
assert.equal(button(tree, "按已存设置启用"), undefined);
assert.equal(button(tree, "保存设置").props.hidden, false);
custom.props.onToggle({ currentTarget: { open: false } });
tree = render();
response = {
  ...resultData,
  rule: { ...resultData.rule, enabled: true, revision: 5 },
};
button(tree, "按已存设置启用").props.onClick();
await tick();
assert.deepEqual(
  JSON.parse(calls.find((item) => item[1]?.method === "PUT")[1].body),
  {
    ...helpers.automaticAlertDraft(resultData.rule),
    enabled: true,
    expected_revision: 4,
  },
);

reset();
resultData = {
  ...shortFixture(),
  rule: { ...shortFixture().rule, ...quickDraft, revision: 6, max_risk_pct: 8 },
};
render();
tree = render();
const customDetails = flatten(tree).find(
  (item) => item.props.className === "automatic-price-alert-custom",
);
assert.ok(button(tree, "关闭自动提醒"));
assert.equal(button(customDetails, "关闭自动提醒"), undefined);
customDetails.props.onToggle({ currentTarget: { open: true } });
button(tree, "3%").props.onClick();
inputs(tree)[2].props.onChange({ target: { checked: false } });
tree = render();
const closeButton = button(tree, "关闭自动提醒");
response = {
  ...resultData,
  rule: { ...resultData.rule, enabled: false, revision: 7 },
};
closeButton.props.onClick();
closeButton.props.onClick();
await tick();
assert.deepEqual(
  calls.filter((item) => item[1]?.method === "PUT").map(([, options]) => JSON.parse(options.body)),
  [{ ...quickDraft, enabled: false, max_risk_pct: 8, expected_revision: 6 }],
);
assert.deepEqual(calls.find((item) => item[0] === "toast"), ["toast", "已关闭自动量价提醒。"]);
assert.equal(calls.filter((item) => item[0] === "close-editor").length, 1);

reset();
render();
tree = render();
response = resultData;
button(tree, "关闭自动提醒").props.onClick();
await tick();
tree = render();
assert.ok(flatten(tree).some((item) => item.type === "ErrorBox" && item.props.message.includes("开关状态未更新")));
assert.equal(calls.some((item) => ["toast", "close-editor"].includes(item[0])), false);

console.log(
  "automatic price alert settings: one-click disable preserves saved preferences; save, reset, revision and late-response safety passed",
);
