import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = ts.transpileModule(
  await fs.readFile(
    new URL("./useHistoricalPrefetch.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const { previousPublishedDays, outsideAuctionWindow, marketPrimaryReadPath, sameDayMarketPaths, useHistoricalPrefetch } = await import(
  encode(
    source
      .replaceAll(
        '"react"',
        JSON.stringify(
          encode(
            `export const createContext=()=>({});
            export const useContext=()=>globalThis.prefetchFixture.plan;
            export const useEffect=(effect)=>{globalThis.prefetchFixture.effect=effect;};
            export const useMemo=(derive)=>derive();
            export const useRef=(current)=>({current});`,
          ),
        ),
      )
      .replaceAll(
        '"../../lib/api"',
        JSON.stringify(
          encode(
            `export const historicalPrefetchBusy=()=>globalThis.prefetchFixture.busy;
            export const prefetchMarketReads=(...args)=>{globalThis.prefetchFixture.calls.push(args);return Promise.resolve(true);};
            export const subscribeReadChanges=(callback)=>{globalThis.prefetchFixture.change=callback;return()=>{globalThis.prefetchFixture.change=null;};};
            export const usePageActive=()=>globalThis.prefetchFixture.active;
            export const firstGainersReadPath=(day)=>'/market/gainers?date='+day+'&page=1&page_size=25&query=&sort_by=change_30d_pct&sort_order=desc&top_n=100';`,
          ),
        ),
      ),
  )
);

assert.deepEqual(
  previousPublishedDays("2026-11-10", [
    "2026-11-09",
    "2026-11-06",
    "2026-11-11",
    "2026-11-10",
    "2026-11-09",
    "2026-11-05",
  ]),
  ["2026-11-09", "2026-11-06"],
);
assert.deepEqual(previousPublishedDays("2026-11-07", ["2026-11-06"]), []);
assert.equal(outsideAuctionWindow(new Date("2026-11-10T09:22:59+08:00")), true);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:23:00+08:00")),
  false,
);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:25:30+08:00")),
  false,
);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:29:59+08:00")),
  false,
);
assert.equal(outsideAuctionWindow(new Date("2026-11-10T09:30:00+08:00")), true);

const day = "2026-11-10";
const previous = "2026-11-09";
const routes = [
  "market", "auction", "intraday", "noon", "news-events", "indices", "emotion",
  "sectors", "ladder", "dragon-tiger", "gainers", "abnormal",
];
const path = marketPrimaryReadPath("market", day);
const currentPaths = sameDayMarketPaths(new Set(routes), "market", day, () => day);
assert.deepEqual(currentPaths, [
  "/market/auction?date=2026-11-10",
  "/market/intraday?date=2026-11-10",
  "/market/noon?date=2026-11-10",
  "/market/news-events?date=2026-11-10",
  "/market/indices?date=2026-11-10",
  "/market/emotion?date=2026-11-10&limit=1000",
  "/market/sectors?date=2026-11-10&limit=1000",
  "/market/ladder?date=2026-11-10&limit=1000",
  "/market/dragon-tiger?date=2026-11-10",
  "/market/gainers?date=2026-11-10&page=1&page_size=25&query=&sort_by=change_30d_pct&sort_order=desc&top_n=100",
  "/market/abnormal?monitor_scope=latest&limit=1000&date=2026-11-10",
]);
const liveRoutes = new Set(["auction", "intraday", "noon", "news-events"]);
assert.deepEqual(
  sameDayMarketPaths(routes, "market", previous, (route) =>
    liveRoutes.has(route) ? day : previous,
  ),
  routes.slice(5).map((route) => marketPrimaryReadPath(route, previous)),
  "before publication warm only routes whose normal observation date matches",
);
assert.deepEqual(
  sameDayMarketPaths(routes, "intraday", day, (route) =>
    liveRoutes.has(route) ? day : previous,
  ),
  ["auction", "noon", "news-events"].map((route) => marketPrimaryReadPath(route, day)),
  "opening today's saved intraday view does not pull yesterday's quote pages",
);

const NativeDate = Date;
globalThis.Date = class extends NativeDate {
  constructor(...args) {
    super(...(args.length ? args : ["2026-11-10T20:00:00+08:00"]));
  }
};
globalThis.prefetchFixture = {
  plan: { date: day, sourcePath: path, currentPaths, marketSnapshotId: "published-market" },
  active: true,
  busy: false,
  calls: [],
};
const listeners = new Map();
const timers = new Map();
let nextTimer = 0;
globalThis.document = {
  hidden: false,
  visibilityState: "visible",
  addEventListener: (name, callback) => listeners.set(name, callback),
  removeEventListener: (name) => listeners.delete(name),
};
globalThis.window = {
  setTimeout(callback, delay) {
    assert.equal(delay, 1800);
    timers.set(++nextTimer, callback);
    return nextTimer;
  },
  clearTimeout: (id) => timers.delete(id),
};
const runIdle = () => {
  const [id, callback] = timers.entries().next().value;
  timers.delete(id);
  callback();
};
const fixture = globalThis.prefetchFixture;

useHistoricalPrefetch(false, path, day, undefined, [previous, day], false);
fixture.effect();
assert.equal(timers.size, 0, "do not start before foreground settles");

useHistoricalPrefetch(false, path, day, undefined, [previous, day], true);
let cleanup = fixture.effect();
assert.equal(fixture.calls.length, 0);
fixture.busy = true;
runIdle();
assert.equal(fixture.calls.length, 0);
fixture.busy = false;
runIdle();
assert.deepEqual(fixture.calls[0].slice(0, 4), [path, currentPaths, [], "published-market"]);
const signal = fixture.calls[0][4];
listeners.get("pointerdown")();
assert.equal(signal.aborted, true, "foreground input cancels a warming queue");
cleanup();
assert.equal(timers.size, 0, "navigation clears deferred work");

useHistoricalPrefetch(true, path, day, "source-archive", [previous, day], true);
cleanup = fixture.effect();
runIdle();
assert.deepEqual(fixture.calls[1].slice(0, 4), [path, currentPaths, [previous], "published-market"]);
fixture.change([{ scope: "intraday" }]);
assert.equal(fixture.calls[1][4].aborted, true, "capture activity cancels the active queue");
document.hidden = true;
listeners.get("visibilitychange")();
assert.equal(timers.size, 0, "background tabs do not schedule warm requests");
cleanup();
assert.equal(fixture.change, null);
globalThis.Date = NativeDate;
