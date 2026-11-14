import assert from "node:assert/strict";
import fs from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const hooks = encode(
  `let cells=[],cursor=0,pending=[];export function reset(){for(const cell of cells)cell?.cleanup?.();cells=[];pending=[];}export function begin(){cursor=0;pending=[];}export function flush(){for(const callback of pending)callback();pending=[];}export function useState(initial){const i=cursor++;if(!(i in cells))cells[i]=typeof initial==='function'?initial():initial;return[cells[i],v=>{cells[i]=typeof v==='function'?v(cells[i]):v;}];}export function useRef(value){const i=cursor++;return cells[i]||(cells[i]={current:value});}export function useEffect(callback,deps){const i=cursor++,old=cells[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j])))pending.push(()=>{old?.cleanup?.();cells[i]={deps,cleanup:callback()};});}`,
);
const api = encode(
  `export const list=value=>value?.items||[];export function usePageActive(){return globalThis.__notice.active;}export function useApi(path){const s=globalThis.__notice;s.paths.push(path);return{data:path?s.data[path]||null:null,error:path?s.errors[path]||'':'',loading:false,reload(){s.reloads.push(path);}};}`,
);
const ui = encode(
  `export function useToast(){return(...args)=>globalThis.__notice.toasts.push(args);}`,
);
const icons = encode(
  `import{jsx}from ${JSON.stringify(runtime)};const Icon=()=>jsx('i',{});export{Icon as BellRing,Icon as X};`,
);
const presentation = encode(
  `export const lineAlertSource=()=> '手动画线';export const lineAlertView=()=>({title:'收盘突破',tone:'triggered'});`,
);
const events = encode(
  `export const AUTOMATIC_PRICE_ALERTS_CHANGED='automatic-price-alerts-changed';export const LINE_ALERT_CHECK_FINISHED='line-alert-check-finished';`,
);
const helper = encode(
  ts.transpileModule(
    await fs.readFile(
      new URL("./lineAlertNotification.ts", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ES2022,
      },
    },
  ).outputText,
);
let source = ts
  .transpileModule(
    await fs.readFile(
      new URL("./LineAlertNotice.tsx", import.meta.url),
      "utf8",
    ),
    {
      fileName: "LineAlertNotice.tsx",
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  )
  .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const modules = {
  react: hooks,
  "../../lib/api": api,
  "../../components/UI": ui,
  "lucide-react": icons,
  "../../lib/workbenchEvents": events,
  "./lineAlertNotification": helper,
  "./lineAlertPresentation": presentation,
  "react/jsx-runtime": runtime,
};
for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)])
  source = source.replace(
    match[0],
    "from " +
      JSON.stringify(modules[match[2]] || import.meta.resolve(match[2])),
  );
const {
  default: Notice,
  currentIntradayNoticeSummary,
  intradayNoticeTime,
} = await import(encode(source));
const { reset, begin, flush } = await import(hooks);
const day = "2026-11-16",
  businessDate = "2026-11-13";
const event = {
  event_id: "live-one",
  instrument_id: "600001.SH",
  name: "甲股票",
  code: "600001",
  trade_date: day,
  title: "回踩支撑后收回",
  first_seen_at_ms: Date.parse(day + "T02:00:00Z"),
  last_seen_at_ms: Date.parse(day + "T02:10:00Z"),
  sources: [{ source_kind: "manual" }, { source_kind: "automatic" }],
};
const live = {
  date: day,
  enabled: true,
  chart_as_of: businessDate,
  items: [event],
  total: 3,
  unread_count: 2,
  notice: "这是盘中采样，非收盘确认。",
};
assert.equal(currentIntradayNoticeSummary(live, day), live);
for (const bad of [
  null,
  { ...live, date: businessDate },
  { ...live, enabled: 1 },
  { ...live, unread_count: 4 },
  { ...live, unread_count: -1 },
  { ...live, total: NaN },
  { ...live, items: null },
  { ...live, items: Array(6).fill(event) },
  { ...live, items: [{ ...event, trade_date: businessDate }] },
])
  assert.equal(currentIntradayNoticeSummary(bad, day), null);
assert.equal(
  currentIntradayNoticeSummary(live),
  null,
  "server current day is required",
);
assert.equal(
  currentIntradayNoticeSummary(
    { ...live, date: "2026-02-30", total: 0, unread_count: 0, items: [] },
    "2026-02-30",
  ),
  null,
);
assert.equal(intradayNoticeTime(Date.parse(day + "T02:10:00Z")), "10:10:00");
assert.equal(intradayNoticeTime(null), "—");
const liveWithServerTime = {
  ...live,
  server_now_ms: Date.parse(day + "T02:10:00Z"),
};
assert.equal(
  currentIntradayNoticeSummary(liveWithServerTime),
  liveWithServerTime,
  "server clock supplies today's scope before the shell knows it",
);
assert.equal(
  currentIntradayNoticeSummary(liveWithServerTime, businessDate),
  liveWithServerTime,
  "a newer server day supersedes an older shell receipt",
);
assert.equal(
  currentIntradayNoticeSummary(liveWithServerTime, "2026-11-17"),
  null,
  "an older server receipt cannot supersede a newer shell day",
);
assert.equal(
  currentIntradayNoticeSummary({
    ...liveWithServerTime,
    server_now_ms: Date.parse(day + "T16:01:00Z"),
  }),
  null,
  "summary date must agree with the Shanghai date of the server clock",
);
for (const server_now_ms of [null, -1, Number.NaN, Infinity, "2026-11-16"]) {
  assert.equal(
    currentIntradayNoticeSummary({ ...live, server_now_ms }, day),
    null,
    "a malformed explicit server clock cannot fall back to the shell day",
  );
}

const oldWindow = globalThis.window,
  oldDocument = globalThis.document;
const windowListeners = new Map(),
  documentListeners = new Map(),
  timers = new Map(),
  timeouts = new Map();
let timerId = 0;
function listen(map, type, callback) {
  if (!map.has(type)) map.set(type, new Set());
  map.get(type).add(callback);
}
function unlisten(map, type, callback) {
  map.get(type)?.delete(callback);
}
function fire(map, type, event = {}) {
  for (const listener of map.get(type) || []) listener(event);
}
globalThis.window = {
  location: { hash: "" },
  addEventListener: (type, fn) => listen(windowListeners, type, fn),
  removeEventListener: (type, fn) => unlisten(windowListeners, type, fn),
  dispatchEvent(event) {
    fire(windowListeners, event.type, event);
    return true;
  },
  setInterval(callback, interval) {
    const id = ++timerId;
    timers.set(id, { callback, interval });
    return id;
  },
  clearInterval(id) {
    timers.delete(id);
  },
  setTimeout(callback, interval) {
    const id = ++timerId;
    timeouts.set(id, { callback, interval });
    return id;
  },
  clearTimeout(id) {
    timeouts.delete(id);
  },
};
globalThis.document = {
  visibilityState: "visible",
  addEventListener: (type, fn) => listen(documentListeners, type, fn),
  removeEventListener: (type, fn) => unlisten(documentListeners, type, fn),
};
globalThis.__notice = {
  active: true,
  paths: [],
  reloads: [],
  errors: {},
  toasts: [],
  data: {
    "/intraday-monitor/summary": live,
    "/watchlist/line-alerts/summary": {
      business_date: businessDate,
      total: 123,
      items: [{ event_id: "daily-one", name: "盘后股票", code: "600002" }],
    },
  },
};
const state = globalThis.__notice;
const navigated = [];
let today = day;
function walk(node, predicate) {
  if (Array.isArray(node))
    return node.flatMap((child) => walk(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [
    ...(predicate(node) ? [node] : []),
    ...walk(node.props.children, predicate),
  ];
}
const find = (tree, predicate) => walk(tree, predicate)[0];
const caption = (node) => renderToStaticMarkup(node).replace(/<[^>]*>/g, "");
const badgeCaption = (trigger) => {
  const badge = find(trigger, (node) => node.type === "b");
  return badge ? caption(badge) : "";
};
function control(tree, label) {
  const item = find(
    tree,
    (node) => node.type === "button" && caption(node) === label,
  );
  assert.ok(item, label);
  return item;
}
function render() {
  begin();
  const tree = Notice({
    businessDate,
    today,
    onNavigate: (route) => navigated.push(route),
  });
  flush();
  return {
    tree,
    html: renderToStaticMarkup(tree),
    trigger: find(
      tree,
      (node) =>
        node.type === "button" &&
        node.props["aria-controls"] === "today-line-alerts-popover",
    ),
  };
}
try {
  let result = render();
  assert.deepEqual(state.toasts, [], "first load never toasts saved history");
  assert.match(result.trigger.props["aria-label"], /盘中未读 2 条（2026-11-16/);
  assert.match(
    result.trigger.props["aria-label"],
    /盘后行情提醒 123 条（业务日 2026-11-13/,
  );
  assert.equal(
    badgeCaption(result.trigger),
    "2",
    "enabled monitor badge uses live unread, not prior-business-day count",
  );
  assert.match(result.trigger.props.className, /has-unread/);
  assert.match(result.trigger.props.className, /can-animate/);
  assert.match(caption(result.trigger), /未读2/);
  assert.deepEqual(
    state.toasts,
    [],
    "reading or changing tabs is not a new event",
  );
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].interval, 30_000);
  [...timers.values()][0].callback();
  assert.deepEqual(
    state.reloads,
    ["/intraday-monitor/summary"],
    "timer only reads the compact live summary",
  );
  result.trigger.props.onClick();
  result = render();
  assert.doesNotMatch(result.trigger.props.className, /can-animate/);
  assert.equal(
    find(
      result.tree,
      (node) => node.props.role === "tab" && caption(node).startsWith("盘中"),
    ).props["aria-selected"],
    true,
  );
  assert.match(result.html, /首次采样 10:00:00 · 最近采样 10:10:00 · 2 项依据/);
  assert.equal(state.reloads.at(-2), "/watchlist/line-alerts/summary");
  assert.equal(state.reloads.at(-1), "/intraday-monitor/summary");

  // A manual tab choice survives both sampling updates and setting changes.
  find(
    result.tree,
    (node) => node.props.role === "tab" && caption(node).startsWith("盘后"),
  ).props.onClick();
  state.data["/intraday-monitor/summary"] = { ...live, unread_count: 3 };
  result = render();
  assert.match(result.html, /盘后股票/);
  assert.doesNotMatch(result.html, /回踩支撑后收回/);
  assert.equal(badgeCaption(result.trigger), "3");
  control(result.tree, "查看今日提醒").props.onClick();
  assert.equal(navigated.at(-1), "watchlist");
  assert.equal(globalThis.window.location.hash, "watchlist?alerts=today");
  result = render();
  result.trigger.props.onClick();
  result = render();
  find(
    result.tree,
    (node) => node.props.role === "tab" && caption(node).startsWith("盘中"),
  ).props.onClick();
  result = render();
  control(result.tree, "查看盘中监控").props.onClick();
  assert.equal(navigated.at(-1), "monitor");

  state.data["/intraday-monitor/summary"] = { ...live, enabled: false };
  result = render();
  assert.equal(
    badgeCaption(result.trigger),
    "2",
    "saved unread alerts remain visible when automatic capture is disabled",
  );
  assert.match(result.trigger.props.className, /has-unread/);
  state.errors["/watchlist/line-alerts/summary"] = "daily unavailable";
  result = render();
  assert.equal(
    badgeCaption(result.trigger),
    "2",
    "daily failures cannot mask live unread",
  );
  state.errors = {};
  const beforeChanged = state.reloads.length;
  globalThis.window.dispatchEvent(new Event("intraday-monitor-changed"));
  assert.deepEqual(state.reloads.slice(beforeChanged), [
    "/intraday-monitor/summary",
  ]);

  // Bad or yesterday's live summary is visibly unknown, never a false zero.
  state.data["/intraday-monitor/summary"] = { ...live, date: businessDate };
  result = render();
  assert.equal(badgeCaption(result.trigger), "?");
  assert.match(result.trigger.props.title, /盘中未读状态待确认/);
  state.errors["/intraday-monitor/summary"] = "network";
  result = render();
  assert.equal(badgeCaption(result.trigger), "!");
  assert.match(result.trigger.props.title, /盘中未读读取失败/);
  state.errors = {};
  state.data["/intraday-monitor/summary"] = {
    ...live,
    total: 0,
    unread_count: 0,
    items: [],
  };
  result = render();
  assert.equal(
    find(result.trigger, (node) => node.type === "b"),
    undefined,
    "verified zero unread has no number badge",
  );
  assert.doesNotMatch(result.trigger.props.className, /has-unread|can-animate/);
  state.data["/intraday-monitor/summary"] = {
    ...live,
    enabled: false,
    unread_count: 0,
  };
  result = render();
  assert.equal(badgeCaption(result.trigger), "99+");
  assert.match(caption(result.trigger), /盘后99\+/);
  assert.match(result.trigger.props.className, /has-daily/);
  assert.doesNotMatch(result.trigger.props.className, /has-unread|can-animate/);
  state.data["/intraday-monitor/summary"] = {
    ...live,
    total: 123,
    unread_count: 123,
  };
  result = render();
  assert.equal(
    badgeCaption(result.trigger),
    "99+",
    "live unread uses a bounded badge",
  );

  // No hidden or inactive polling, including late timer callbacks.
  const timer = [...timers.values()][0];
  const beforeHidden = state.reloads.length;
  globalThis.document.visibilityState = "hidden";
  timer.callback();
  fire(documentListeners, "visibilitychange");
  result = render();
  assert.equal(timers.size, 0);
  assert.equal(state.paths.at(-1), null);
  globalThis.window.dispatchEvent(new Event("intraday-monitor-changed"));
  assert.equal(state.reloads.length, beforeHidden);
  globalThis.document.visibilityState = "visible";
  fire(documentListeners, "visibilitychange");
  state.active = false;
  result = render();
  assert.equal(timers.size, 0);
  assert.equal(state.paths.at(-1), null);

  // With no prior user choice, saved unread takes priority even if capture is off.
  reset();
  timers.clear();
  state.active = true;
  state.data["/intraday-monitor/summary"] = { ...live, enabled: false };
  result = render();
  result.trigger.props.onClick();
  result = render();
  assert.equal(
    find(
      result.tree,
      (node) => node.props.role === "tab" && caption(node).startsWith("盘中"),
    ).props["aria-selected"],
    true,
  );
  state.data["/intraday-monitor/summary"] = live;
  result = render();
  assert.equal(
    find(
      result.tree,
      (node) => node.props.role === "tab" && caption(node).startsWith("盘中"),
    ).props["aria-selected"],
    true,
  );

  // Toast only newly observed identities, not holding updates, extra evidence
  // receipts or an unread flag changing on an already known event.
  reset();
  timers.clear();
  state.toasts = [];
  state.active = true;
  state.errors = {};
  globalThis.document.visibilityState = "visible";
  today = day;
  function liveEvent(id, tradeDate = day, extra = {}) {
    return {
      ...event,
      event_id: id,
      trade_date: tradeDate,
      first_seen_at_ms: Date.parse(tradeDate + "T02:00:00Z"),
      last_seen_at_ms: Date.parse(tradeDate + "T02:10:00Z"),
      ...extra,
    };
  }
  function liveReceipt(items, tradeDate = day, extra = {}) {
    return {
      ...live,
      date: tradeDate,
      server_now_ms: Date.parse(tradeDate + "T02:10:00Z"),
      items,
      total: items.length,
      unread_count: items.length,
      ...extra,
    };
  }
  const seed = liveEvent("toast-seed");
  state.data["/intraday-monitor/summary"] = liveReceipt([seed]);
  render();
  render();
  assert.equal(state.toasts.length, 0);
  state.data["/intraday-monitor/summary"] = liveReceipt([
    {
      ...seed,
      phase: "holding",
      last_seen_at_ms: seed.last_seen_at_ms + 600_000,
      sources: [...seed.sources, { source_kind: "manual", label: "新增依据" }],
    },
  ]);
  render();
  assert.equal(
    state.toasts.length,
    0,
    "holding and new evidence retain the same event identity",
  );
  const firstNew = liveEvent("toast-new-one");
  state.data["/intraday-monitor/summary"] = liveReceipt([firstNew, seed]);
  render();
  assert.equal(state.toasts.length, 1);
  assert.match(state.toasts[0][0], /新增盘中提醒.*1.*点击铃铛查看/);
  render();
  result = render();
  assert.match(result.trigger.props.className, /has-new-arrival/);
  assert.equal(
    timeouts.size,
    1,
    "a newly discovered identity gets one bounded accent",
  );
  const [accentId, accent] = [...timeouts.entries()][0];
  assert.equal(accent.interval, 6_000);
  timeouts.delete(accentId);
  accent.callback();
  result = render();
  assert.doesNotMatch(result.trigger.props.className, /has-new-arrival/);
  assert.match(
    result.trigger.props.className,
    /can-animate/,
    "unread breathing remains after the arrival accent ends",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([firstNew, seed], day, {
    unread_count: 0,
  });
  render();
  assert.equal(
    state.toasts.length,
    1,
    "marking an existing event read is not a new identity",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    liveEvent("toast-new-two"),
    liveEvent("toast-new-three"),
    firstNew,
  ]);
  render();
  assert.equal(
    state.toasts.length,
    2,
    "one update with two new events produces one combined toast",
  );
  render();
  result = render();
  assert.match(result.trigger.props.className, /has-new-arrival/);
  result.trigger.props.onClick();
  render();
  result = render();
  assert.doesNotMatch(
    result.trigger.props.className,
    /can-animate|has-new-arrival/,
  );
  assert.equal(
    timeouts.size,
    0,
    "opening the popover cancels the arrival timeout",
  );
  result.trigger.props.onClick();
  result = render();
  assert.doesNotMatch(
    result.trigger.props.className,
    /has-new-arrival/,
    "closing the popover does not replay arrival motion",
  );
  assert.match(state.toasts[1][0], /新增盘中提醒.*2.*点击铃铛查看/);
  render();
  assert.equal(
    state.toasts.length,
    2,
    "rerendering the same receipt cannot replay the toast",
  );

  // Hiding the page suspends the feed. On return, cached data must not consume
  // the reseed gate before the first newly received server object arrives.
  const visibleReceipt = state.data["/intraday-monitor/summary"];
  globalThis.document.visibilityState = "hidden";
  fire(documentListeners, "visibilitychange");
  render();
  const hiddenNew = liveEvent("toast-hidden-new");
  state.data["/intraday-monitor/summary"] = liveReceipt([hiddenNew, firstNew]);
  render();
  assert.equal(state.toasts.length, 2, "hidden responses never toast");
  globalThis.document.visibilityState = "visible";
  fire(documentListeners, "visibilitychange");
  state.data["/intraday-monitor/summary"] = visibleReceipt;
  render();
  state.data["/intraday-monitor/summary"] = liveReceipt([hiddenNew, firstNew]);
  render();
  assert.equal(
    state.toasts.length,
    2,
    "first fresh response on visibility return is a silent baseline",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    liveEvent("toast-after-visible"),
    hiddenNew,
  ]);
  render();
  assert.equal(
    state.toasts.length,
    3,
    "later visible discoveries resume toast delivery",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    {
      ...seed,
      phase: "holding",
      last_seen_at_ms: seed.last_seen_at_ms + 1_800_000,
    },
    hiddenNew,
  ]);
  render();
  assert.equal(
    state.toasts.length,
    3,
    "an earlier same-day event returning to the five-item summary after resume is still known",
  );

  const activeReceipt = state.data["/intraday-monitor/summary"];
  state.active = false;
  render();
  const inactiveNew = liveEvent("toast-inactive-new");
  state.data["/intraday-monitor/summary"] = liveReceipt([inactiveNew]);
  render();
  assert.equal(state.toasts.length, 3, "inactive pages never toast");
  state.active = true;
  state.data["/intraday-monitor/summary"] = activeReceipt;
  render();
  state.data["/intraday-monitor/summary"] = liveReceipt([inactiveNew]);
  render();
  assert.equal(
    state.toasts.length,
    3,
    "returning from an inactive page does not replay missed events",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    liveEvent("toast-after-active"),
    inactiveNew,
  ]);
  render();
  assert.equal(state.toasts.length, 4);

  state.data["/intraday-monitor/summary"] = liveReceipt(
    [liveEvent("toast-disabled")],
    day,
    { enabled: false },
  );
  render();
  assert.equal(
    state.toasts.length,
    5,
    "newly saved identities remain noticeable after capture is disabled",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    liveEvent("toast-disabled"),
  ]);
  render();
  assert.equal(
    state.toasts.length,
    5,
    "enabling capture does not replay the already observed identity",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt([
    liveEvent("toast-after-enabled"),
  ]);
  render();
  assert.equal(state.toasts.length, 6);

  today = "2026-11-17";
  state.data["/intraday-monitor/summary"] = liveReceipt(
    [liveEvent("toast-next-day-existing", today)],
    today,
  );
  render();
  assert.equal(
    state.toasts.length,
    6,
    "the first valid receipt of a new server day is silent",
  );
  state.data["/intraday-monitor/summary"] = liveReceipt(
    [liveEvent("toast-next-day-new", today)],
    today,
  );
  render();
  assert.equal(state.toasts.length, 7);
  assert.doesNotMatch(
    source,
    /method:\s*["'](?:POST|PUT)|\/evaluate|AudioContext|Notification\./,
  );
  state.data["/intraday-monitor/summary"] = liveReceipt(
    Array.from({ length: 5 }, (_, index) =>
      liveEvent(`scroll-${index}`, today),
    ),
    today,
  );
  result = render();
  result.trigger.props.onClick();
  result = render();
  const scrollList = find(
    result.tree,
    (node) =>
      node.type === "ul" &&
      node.props["aria-label"] === "近期盘中提醒，可在列表内滚动",
  );
  assert.ok(scrollList);
  assert.equal(
    scrollList.props.tabIndex,
    0,
    "the bounded list supports keyboard scrolling",
  );
  assert.equal(
    walk(scrollList, (node) => node.type === "li").length,
    5,
    "three visible rows do not discard the remaining summary records",
  );
  assert.match(result.html, /最近 5 条摘要/);
  reset();
  assert.equal(
    timeouts.size,
    0,
    "unmount clears any outstanding arrival timeout",
  );
  const css = await fs.readFile(
    new URL("./LineAlertNotice.css", import.meta.url),
    "utf8",
  );
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(
    css,
    /\.line-alert-notice-trigger\.can-animate[\s\S]*animation: none/,
  );
  assert.doesNotMatch(
    css,
    /var\(--up\)/,
    "badges and errors use defined palette colors",
  );
  console.log(
    "line notice: dual-date tabs, server-clock scope, legacy daily fallback, visible-only reads and identity-deduplicated new-event toasts passed",
  );
} finally {
  reset();
  globalThis.window = oldWindow;
  globalThis.document = oldDocument;
  delete globalThis.__notice;
}
