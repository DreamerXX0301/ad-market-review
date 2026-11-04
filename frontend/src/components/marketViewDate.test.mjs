// Execute the real App's date/navigation callbacks with controlled local GET results.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const slots = [],
  effects = [],
  listeners = new Map(),
  timers = new Map();
let cursor = 0,
  pending = [],
  changed = false,
  tree,
  timerId = 0;
const same = (a, b) =>
  a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
const hooks = {
  memo: (component) => component,
  useCallback: (fn, deps) => {
    const i = cursor++;
    if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn, deps };
    return slots[i].value;
  },
  useState(initial) {
    const i = cursor++;
    if (!(i in slots))
      slots[i] = typeof initial === "function" ? initial() : initial;
    return [
      slots[i],
      (next) => {
        const value = typeof next === "function" ? next(slots[i]) : next;
        if (!Object.is(value, slots[i])) {
          slots[i] = value;
          changed = true;
        }
      },
    ];
  },
  useEffect(fn, deps) {
    const i = cursor++;
    if (!effects[i] || !same(effects[i].deps, deps))
      pending.push(() => {
        effects[i]?.cleanup?.();
        effects[i] = { deps, cleanup: fn() };
      });
  },
};
globalThis.__dateHooks = hooks;
globalThis.location = { hash: "#market" };
globalThis.document = {
  hidden: false,
  documentElement: { dataset: {} },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = {
  history: {
    state: null,
    replaceState(_state, _title, hash) {
      location.hash = hash;
    },
  },
  addEventListener(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
  },
  removeEventListener(name, fn) {
    listeners.get(name)?.delete(fn);
  },
  dispatchEvent(event) {
    for (const fn of listeners.get(event.type) || []) fn(event);
  },
  setInterval(fn) {
    timers.set(++timerId, fn);
    return timerId;
  },
  clearInterval(id) {
    timers.delete(id);
  },
};
const days = ["2026-10-27", "2026-10-28", "2026-10-29"];
const packets = {
  "/bootstrap": { data_date: days.at(-1), market_dates: days },
  "/market/auction/dates": null,
  "/market/noon/dates": null,
};
const reloads = [],
  callbacks = new Map();
globalThis.__dateApi = (path) => {
  if (!callbacks.has(path)) callbacks.set(path, () => reloads.push(path));
  return {
    data: packets[path] || null,
    loading: !!path && !packets[path],
    error: "",
    reload: callbacks.get(path),
  };
};
const routes = [
  "market",
  "auction",
  "noon",
  "intraday",
  "indices",
  "emotion",
  "sectors",
  "ladder",
  "dragon-tiger",
  "gainers",
  "abnormal",
];
const replacements = {
  react: encode(
    "export const {memo,useState,useEffect,useCallback}=globalThis.__dateHooks;",
  ),
  "./lib/api": encode(
    "export const useApi=globalThis.__dateApi;export const ApiPageActivity={Provider:'activity'};",
  ),
  "./components/UI": encode(
    "export const ToastContext={Provider:'toast'};export const Empty='empty',ErrorBox='error',Loading='loading',Panel='panel';",
  ),
  "./components/AppShell": encode(
    `export default 'shell';export const marketRoutes=new Set(${JSON.stringify(routes)});export const routeTitles=Object.fromEntries(${JSON.stringify([...routes, "topics", "strategy", "tracking", "data", "sources", "research", "backtest", "zodiac", "watchlist"])}.map(key=>[key,key]));`,
  ),
  "./components/LazyView": encode("export const lazyView=()=> 'page';"),
  "./components/IncrementProgressDialog": encode("export default 'progress';"),
  "./components/ReadonlyPreviewBanner": encode("export default 'preview';"),
};
const cache = new Map();
async function moduleUrl(url, overrides = replacements) {
  const key = url.href + (overrides === replacements ? "app" : "real");
  if (cache.has(key)) return cache.get(key);
  let source = ts
    .transpileModule(await fs.readFile(url, "utf8"), {
      fileName: url.pathname,
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    .outputText.replace(/^import ["'][^"']+\.css["'];?$/gm, "")
    .replaceAll("import.meta.env.VITE_READONLY_PREVIEW", '"0"');
  for (const name of [
    ...new Set(
      [...source.matchAll(/from\s+["']([^"']+)["']/g)].map((match) => match[1]),
    ),
  ]) {
    let target = overrides[name];
    if (!target && name.startsWith(".")) {
      for (const extension of [".ts", ".tsx"]) {
        const candidate = new URL(name + extension, url);
        try {
          await fs.access(candidate);
          target = await moduleUrl(candidate, overrides);
          break;
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
      assert.ok(target, name);
    }
    target ||= import.meta.resolve(name);
    source = source
      .replaceAll('from "' + name + '"', "from " + JSON.stringify(target))
      .replaceAll("from '" + name + "'", "from " + JSON.stringify(target));
  }
  const result = encode(source);
  cache.set(key, result);
  return result;
}
const { default: App } = await import(
  await moduleUrl(new URL("../App.tsx", import.meta.url))
);
function render() {
  for (let count = 0; count < 12; count++) {
    cursor = 0;
    pending = [];
    changed = false;
    tree = App();
    for (const fn of pending) fn();
    if (!changed) return;
  }
  throw Error("App effects did not settle");
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object" || !node.props) return [];
  return [node, ...elements(node.props.children)];
}
const shell = () => elements(tree).find((node) => node.type === "shell").props;
const progress = () =>
  elements(tree).find((node) => node.type === "progress").props;
function navigate(route) {
  shell().onNavigate(route);
  render();
  return shell();
}
function reset(hash = "#market") {
  for (const effect of effects) effect?.cleanup?.();
  effects.length = slots.length = 0;
  location.hash = hash;
  render();
}
render();
assert.equal(shell().date, "2026-10-29");
assert.equal(
  navigate("auction").date,
  "",
  "first entry waits for a verified server day, never queries yesterday by default",
);
assert.ok(
  elements(tree).some((node) => node.props.title === "正在核验今天的日期"),
);
packets["/market/auction/dates"] = {
  today: "2026-10-30",
  dates: ["2026-10-29"],
};
render();
assert.equal(shell().date, "2026-10-30");
assert.equal(navigate("indices").date, "2026-10-29");
assert.equal(
  navigate("noon").date,
  "2026-10-30",
  "returning reviews reuse the verified server day without unmounting their retained page",
);
packets["/market/noon/dates"] = { today: "2026-10-30", dates: ["2026-10-29"] };
render();
assert.equal(navigate("market").date, "2026-10-29");
assert.equal(navigate("auction").date, "2026-10-30");
shell().onDate("2026-10-28");
render();
for (const route of [...routes, "strategy", "data", "noon", "auction"])
  assert.equal(
    navigate(route).date,
    "2026-10-28",
    `manual date survives ${route}`,
  );
progress().onNoonFinished({ target_date: "2026-10-30" });
render();
assert.equal(
  shell().date,
  "2026-10-28",
  "completion cannot override manual history",
);
assert.equal(shell().dateFixed, true);
shell().onResetDate();
render();
assert.equal(shell().date, "2026-10-30");
assert.equal(navigate("indices").date, "2026-10-29");
// Even manually pinning the current latest day must survive a new publication.
shell().onDate("2026-10-29");
render();
packets["/bootstrap"] = {
  data_date: "2026-10-30",
  market_dates: [...days, "2026-10-30"],
};
render();
assert.equal(shell().date, "2026-10-29");
shell().onResetDate();
render();
assert.equal(shell().date, "2026-10-30");
// Missing close data never silently adopts an older day after an explicit selection.
shell().onDate("2026-11-02");
render();
assert.equal(shell().date, "2026-11-02");
assert.ok(
  elements(tree).some((node) => node.props.title === "该日盘后行情尚未发布"),
);
shell().onResetDate();
render();
packets["/bootstrap"] = { data_date: "2026-10-29", market_dates: days };
progress().onAuctionFinished({ target_date: "2026-10-30" });
render();
assert.equal(shell().date, "2026-10-30");
assert.equal(
  navigate("market").date,
  "2026-10-29",
  "automatic result does not drag a missing close date into base data",
);
reset("#intraday?date=2026-10-28");
assert.equal(shell().date, "2026-10-28");
shell().onResetDate();
render();
assert.equal(location.hash, "#intraday");
assert.equal(shell().date, "2026-10-30");
assert.ok(elements(tree).some((node) => node.type === "page" && node.props.date === "2026-10-30"), "intraday loads before the daily close is published");
reset("#noon?date=2026-10-28");
assert.equal(shell().date, "2026-10-28");
shell().onDate("2026-10-29");
render();
assert.equal(location.hash, "#noon?date=2026-10-29");
location.hash = "#auction?date=2026-10-27";
window.dispatchEvent(new Event("hashchange"));
render();
assert.equal(shell().date, "2026-10-27", "in-app deep links are explicit, too");
shell().onResetDate();
render();
assert.equal(
  location.hash,
  "#auction",
  "returning to today removes the old history link before a browser refresh",
);
packets["/market/auction/dates"] = {
  today: "2026-10-31",
  dates: ["2026-10-29"],
};
for (const fn of timers.values()) fn();
render();
assert.equal(
  shell().date,
  "2026-10-31",
  "default review follows the refreshed server date on a weekend, without inventing a trade day",
);
assert.equal(navigate("market").date, "2026-10-29");
assert.ok(reloads.includes("/market/auction/dates"));
packets["/market/noon/dates"] = { today: "2026-10-30", dates: ["2026-10-29"] };
reset("#indices");
assert.equal(shell().date, "2026-10-29");
assert.equal(
  shell().today,
  "2026-10-30",
  "a direct base-market visit obtains the server date without first visiting a review",
);
for (const fn of timers.values()) fn();
assert.ok(
  reloads.includes("/market/noon/dates"),
  "base pages refresh only the small date directory, not all market data",
);
const { default: Notice } = await import(
  await moduleUrl(new URL("./MarketDateNotice.tsx", import.meta.url), {})
);
// Follow the real acknowledgement callback through refreshed bootstrap data
// and render its resulting notice. These dates are test-only API fixtures.
const currentNotice = () => {
  const state = shell();
  return renderToStaticMarkup(
    createElement(Notice, {
      date: state.date,
      today: state.today,
      latest: state.dataDate,
      intraday: ["auction", "noon", "intraday"].includes(state.route),
      fixed: state.dateFixed,
      published: state.datePublished,
      onReset: state.onResetDate,
    }),
  );
};
const beforePublication = () => {
  packets["/bootstrap"] = { data_date: "2026-10-29", market_dates: days };
  packets["/market/auction/dates"] = {
    today: "2026-10-30",
    dates: ["2026-10-29"],
  };
  reset("#indices");
};
const publishedToday = {
  data_date: "2026-10-30",
  market_dates: [...days, "2026-10-30"],
};
beforePublication();
assert.match(currentNotice(), /最新已发布行情（非今日）/);
const beforeAcknowledgement = reloads.length;
progress().onFinished();
render();
assert.equal(
  reloads.slice(beforeAcknowledgement).filter((path) => path === "/bootstrap").length,
  1,
  "acknowledging an increment triggers one bootstrap reread",
);
assert.equal(shell().date, "2026-10-29");
assert.match(currentNotice(), /非今日/,
  "acknowledgement alone cannot fabricate today's published date");
packets["/bootstrap"] = publishedToday;
render();
assert.equal(shell().date, "2026-10-30");
assert.match(currentNotice(), /最新已发布行情/);
assert.doesNotMatch(currentNotice(), /非今日|is-history|正在查看历史数据/);
for (const route of [...routes, "strategy", "tracking", "research", "backtest", "data", "indices"]) {
  assert.equal(navigate(route).date, "2026-10-30", `published day survives ${route}`);
  if (routes.includes(route)) assert.doesNotMatch(currentNotice(), /非今日|is-history/);
}
beforePublication();
shell().onDate("2026-10-29");
render();
progress().onFinished();
packets["/bootstrap"] = publishedToday;
render();
for (const route of [...routes, "strategy", "data", "noon", "indices"]) {
  assert.equal(navigate(route).date, "2026-10-29", `publication retains manual history on ${route}`);
  assert.equal(shell().dateFixed, true);
  if (routes.includes(route)) assert.match(currentNotice(), /正在查看历史数据/);
}
shell().onResetDate();
render();
assert.equal(shell().date, "2026-10-30");
assert.doesNotMatch(currentNotice(), /非今日|is-history/);
// Failed/no-change/partial chains can also be acknowledged. Only an actual
// newer published date may clear the warning; task status is not a date.
beforePublication();
progress().onFinished();
packets["/bootstrap"] = { data_date: "2026-10-29", market_dates: days };
render();
assert.equal(shell().date, "2026-10-29");
assert.match(currentNotice(), /非今日/);
assert.match(currentNotice(), /is-history/);
const markup = (extra) =>
  renderToStaticMarkup(
    createElement(Notice, {
      date: "2026-10-29",
      today: "2026-10-30",
      latest: "2026-10-29",
      intraday: true,
      fixed: true,
      published: true,
      onReset() {},
      ...extra,
    }),
  );
assert.match(markup({}), /正在查看历史数据/);
assert.match(markup({}), /is-history/);
assert.match(markup({}), /回到今天/);
assert.match(markup({ intraday: false }), /正在查看历史数据/);
assert.match(markup({ intraday: false }), /回到最新/);
assert.match(markup({ intraday: false, fixed: false }), /最新已发布行情/);
assert.match(markup({ intraday: false, fixed: false }), /非今日/);
assert.match(markup({ intraday: false, fixed: false }), /is-history/);
assert.match(markup({ intraday: false, fixed: false }), /今天是 2026-10-30/);
assert.match(
  markup({ intraday: false, fixed: false }),
  /2026-10-29 的历史收盘行情/,
);
assert.doesNotMatch(
  markup({ intraday: false, fixed: false }),
  /回到最新|采集失败|落后.*交易日/,
  "already viewing latest: do not offer a no-op reset or infer a collection failure",
);
assert.doesNotMatch(
  markup({
    intraday: false,
    fixed: false,
    date: "2026-10-30",
    latest: "2026-10-30",
  }),
  /非今日|is-history/,
);
assert.doesNotMatch(
  markup({ intraday: false, fixed: false, today: "" }),
  /非今日|is-history/,
  "unknown server date cannot establish staleness",
);
assert.match(
  markup({ intraday: false, fixed: false, today: "2026-10-31" }),
  /非今日/,
);
assert.doesNotMatch(
  markup({ intraday: false, fixed: false, today: "2026-10-31" }),
  /采集失败|交易日已结束/,
  "a weekend date difference does not establish a missed collection",
);
assert.match(markup({ date: "2026-10-30", fixed: false }), /正在查看今天/);
assert.match(
  markup({ date: "2026-10-30", intraday: false, published: false }),
  /尚无已发布盘后行情/,
);
assert.doesNotMatch(
  markup({ date: "2026-10-30", intraday: false, published: false }),
  /正在查看历史数据/,
);
console.log(
  "App date navigation: automatic today/latest, pinned history, deep links, publication, task completion, midnight and visible notices passed",
);
