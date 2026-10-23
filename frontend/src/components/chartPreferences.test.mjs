/** Display preferences: migration, independent chart kinds and browser storage. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./chartPreferences.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const preferenceModuleUrl =
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64");
const {
  CHART_PREFERENCES_KEY,
  PREVIOUS_CHART_PREFERENCES_KEY,
  EARLIER_CHART_PREFERENCES_KEY,
  LEGACY_CHART_PREFERENCES_KEY,
  createChartPreferenceStore,
  effectiveChartPanels,
  effectiveChartBoll,
  normalizeChartPreferences,
  CHART_INDICATOR_ORDER,
} = await import(preferenceModuleUrl);
const defaults = normalizeChartPreferences(null);
assert.deepEqual(CHART_INDICATOR_ORDER, [
  "volume",
  "boll",
  "kdj",
  "macd",
  "breadth",
]);
assert.equal(defaults.version, 4);
assert.equal(CHART_PREFERENCES_KEY, "shixu-chart-preferences-v4");
assert.deepEqual(effectiveChartPanels(defaults, false), [
  "volume",
  "kdj",
  "macd",
]);
assert.deepEqual(effectiveChartPanels(defaults, true), [
  "volume",
  "kdj",
  "macd",
  "breadth",
]);
assert.equal(effectiveChartBoll(defaults, false), false);
assert.equal(effectiveChartBoll(defaults, true), false);

function browserStorage(entries = []) {
  const values = new Map(entries);
  const writes = [];
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      writes.push(key);
      values.set(key, value);
    },
  };
  return { values, writes, storage };
}

const oldValues = {
  stockMas: [],
  indexMas: [60, 250],
  stockPriceBasis: "forward",
  showLimits: false,
  showEventBaseline: false,
};
for (const [version, key] of [
  [1, LEGACY_CHART_PREFERENCES_KEY],
  [2, EARLIER_CHART_PREFERENCES_KEY],
  [3, PREVIOUS_CHART_PREFERENCES_KEY],
]) {
  const raw = JSON.stringify({
    ...oldValues,
    version,
    mas: [],
    stockShowWaves: false,
    indexShowWaves: false,
    stockWaveMode: "down",
    indexWaveMode: "up",
  });
  const disk = browserStorage([[key, raw]]);
  const store = createChartPreferenceStore(disk.storage);
  const migrated = store.getSnapshot().preferences;
  assert.equal(disk.writes.length, 0, "loading never overwrites an older key");
  assert.equal(migrated.version, 4);
  assert.deepEqual(migrated.stockMas, version === 1 ? [5, 10, 20, 30] : []);
  assert.deepEqual(migrated.indexMas, version === 1 ? [] : [60, 250]);
  assert.equal(migrated.stockPriceBasis, "forward");
  assert.equal(migrated.showLimits, false);
  assert.equal(migrated.showEventBaseline, false);
  assert.equal(migrated.stockShowWaves, version < 3);
  assert.equal(migrated.indexShowWaves, version < 3);
  assert.equal(migrated.stockWaveMode, version === 3 ? "down" : "turning");
  assert.equal(migrated.indexWaveMode, version === 3 ? "up" : "latest");
  assert.deepEqual(migrated.stockPanels, defaults.stockPanels);
  assert.deepEqual(migrated.indexPanels, defaults.indexPanels);
  assert.equal(migrated.stockShowBoll, false);
  assert.equal(migrated.indexShowBoll, false);
  store.update({ stockPanels: [], stockShowBoll: true });
  assert.deepEqual(disk.writes, [CHART_PREFERENCES_KEY]);
  assert.equal(disk.values.get(key), raw, "migration retains the original key");
  const restored = createChartPreferenceStore(disk.storage).getSnapshot()
    .preferences;
  assert.deepEqual(restored.stockPanels, []);
  assert.equal(restored.stockShowBoll, true);
  assert.equal(restored.stockWaveMode, migrated.stockWaveMode);
  assert.equal(restored.stockShowWaves, migrated.stockShowWaves);
  assert.equal(restored.showLimits, false);
}

// Load the newest available format first; saved v4 wins over later old-tab writes.
const disk = browserStorage([
  [LEGACY_CHART_PREFERENCES_KEY, JSON.stringify({ version: 1, mas: [] })],
  [
    EARLIER_CHART_PREFERENCES_KEY,
    JSON.stringify({ version: 2, stockMas: [60] }),
  ],
  [
    PREVIOUS_CHART_PREFERENCES_KEY,
    JSON.stringify({ version: 3, stockMas: [30], stockWaveMode: "up" }),
  ],
]);
const stock = createChartPreferenceStore(disk.storage);
assert.deepEqual(stock.getSnapshot().preferences.stockMas, [30]);
assert.equal(stock.getSnapshot().preferences.stockWaveMode, "up");
let firstChart = 0,
  secondChart = 0;
stock.subscribe(() => firstChart++);
stock.subscribe(() => secondChart++);
stock.update({ stockPanels: ["kdj"], stockShowBoll: true });
assert.equal(firstChart, 1);
assert.equal(secondChart, 1);
assert.deepEqual(
  effectiveChartPanels(stock.getSnapshot().preferences, true),
  defaults.indexPanels,
);
assert.equal(effectiveChartBoll(stock.getSnapshot().preferences, true), false);
stock.update({ indexPanels: [], indexShowBoll: true });
assert.deepEqual(effectiveChartPanels(stock.getSnapshot().preferences, false), [
  "kdj",
]);
assert.equal(effectiveChartBoll(stock.getSnapshot().preferences, false), true);
stock.update({ stockShowBoll: false });
assert.equal(effectiveChartBoll(stock.getSnapshot().preferences, false), false);
assert.equal(effectiveChartBoll(stock.getSnapshot().preferences, true), true);
const saved = stock.getSnapshot().preferences;
const panelCopy = effectiveChartPanels(saved, false);
panelCopy.push("macd");
assert.deepEqual(
  saved.stockPanels,
  ["kdj"],
  "callers cannot mutate the store through helpers",
);
for (const [version, key] of [
  [1, LEGACY_CHART_PREFERENCES_KEY],
  [2, EARLIER_CHART_PREFERENCES_KEY],
  [3, PREVIOUS_CHART_PREFERENCES_KEY],
]) {
  const raw = JSON.stringify({
    version,
    stockPanels: ["macd"],
    stockShowBoll: true,
  });
  disk.values.set(key, raw);
  const before = stock.getSnapshot();
  stock.receiveStorageEvent({ key, newValue: raw, storageArea: disk.storage });
  assert.equal(
    stock.getSnapshot(),
    before,
    "old tab storage events cannot replace v4 preferences",
  );
  assert.deepEqual(
    createChartPreferenceStore(disk.storage).getSnapshot().preferences,
    saved,
  );
}

// Current-key events synchronize tabs without writing back or mixing storage areas.
const otherTab = createChartPreferenceStore(disk.storage);
otherTab.update({
  stockPanels: [],
  stockShowBoll: true,
  indexPanels: ["breadth"],
  indexShowBoll: false,
});
const raw = disk.values.get(CHART_PREFERENCES_KEY);
const writesBefore = disk.writes.length;
stock.receiveStorageEvent({
  key: CHART_PREFERENCES_KEY,
  newValue: raw,
  storageArea: {},
});
assert.deepEqual(
  stock.getSnapshot().preferences,
  saved,
  "session/foreign storage is ignored",
);
stock.receiveStorageEvent({
  key: CHART_PREFERENCES_KEY,
  newValue: raw,
  storageArea: disk.storage,
});
assert.deepEqual(
  stock.getSnapshot().preferences,
  otherTab.getSnapshot().preferences,
);
assert.equal(
  disk.writes.length,
  writesBefore,
  "storage synchronization never loops writes",
);
assert.deepEqual(stock.getSnapshot().preferences.stockPanels, []);
assert.deepEqual(stock.getSnapshot().preferences.indexPanels, ["breadth"]);
stock.receiveStorageEvent({
  key: CHART_PREFERENCES_KEY,
  newValue: null,
  storageArea: disk.storage,
});
assert.deepEqual(stock.getSnapshot().preferences, defaults);
stock.receiveStorageEvent({
  key: null,
  newValue: null,
  storageArea: disk.storage,
});
assert.deepEqual(stock.getSnapshot().preferences, defaults);

// Invalid values cannot introduce unsupported panels or a stock breadth chart.
for (const invalid of [
  null,
  "kdj",
  ["unknown"],
  [false],
  ["volume", "unknown"],
]) {
  const preferences = normalizeChartPreferences({
    version: 4,
    stockPanels: invalid,
    indexPanels: invalid,
  });
  assert.deepEqual(preferences.stockPanels, defaults.stockPanels);
  assert.deepEqual(preferences.indexPanels, defaults.indexPanels);
}
assert.deepEqual(
  normalizeChartPreferences({ version: 4, stockPanels: ["breadth"] })
    .stockPanels,
  defaults.stockPanels,
);
const ordered = normalizeChartPreferences({
  version: 4,
  stockPanels: ["kdj", "volume", "kdj"],
  indexPanels: ["breadth", "kdj"],
});
assert.deepEqual(ordered.stockPanels, ["volume", "kdj"]);
assert.deepEqual(ordered.indexPanels, ["kdj", "breadth"]);
for (const invalid of ["true", "false", 1, {}, null]) {
  const preferences = normalizeChartPreferences({
    version: 4,
    stockShowBoll: invalid,
    indexShowBoll: invalid,
  });
  assert.equal(preferences.stockShowBoll, false);
  assert.equal(preferences.indexShowBoll, false);
}

// Quota/privacy failures retain choices for all chart subscribers in this window.
const unavailable = createChartPreferenceStore({
  getItem() {
    throw new Error("denied");
  },
  setItem() {
    throw new Error("quota");
  },
});
let notifications = 0;
unavailable.subscribe(() => notifications++);
assert.doesNotThrow(() =>
  unavailable.update({
    stockPanels: [],
    stockShowBoll: true,
    indexPanels: ["macd"],
    indexShowBoll: false,
  }),
);
assert.equal(notifications, 1);
assert.deepEqual(unavailable.getSnapshot().preferences.stockPanels, []);
assert.equal(unavailable.getSnapshot().preferences.stockShowBoll, true);
assert.deepEqual(unavailable.getSnapshot().preferences.indexPanels, ["macd"]);
assert.equal(unavailable.getSnapshot().persistent, false);
unavailable.reset();
assert.deepEqual(unavailable.getSnapshot().preferences, defaults);
assert.equal(unavailable.getSnapshot().persistent, false);
const memory = createChartPreferenceStore(null);
memory.update({ stockPanels: [], indexPanels: [], stockShowBoll: true });
assert.deepEqual(memory.getSnapshot().preferences.stockPanels, []);
assert.deepEqual(memory.getSnapshot().preferences.indexPanels, []);
assert.equal(memory.getSnapshot().preferences.stockShowBoll, true);
assert.equal(memory.getSnapshot().persistent, false);

// Exercise the actual singleton hook and its browser event binding. The React
// shim only reads snapshots; store subscription behavior is tested above.
const hookDisk = browserStorage();
const listeners = new Map();
const oldWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
try {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: hookDisk.storage,
      addEventListener: (name, listener) => listeners.set(name, listener),
    },
  });
  const reactShimUrl =
    "data:text/javascript;base64," +
    Buffer.from(
      "export const useSyncExternalStore = (_subscribe, getSnapshot) => getSnapshot();",
    ).toString("base64");
  const hookSource = await fs.readFile(
    new URL("./useChartPreferences.ts", import.meta.url),
    "utf8",
  );
  const hookCompiled = ts
    .transpileModule(hookSource, {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    })
    .outputText.replace(
      /from ["']react["']/g,
      "from " + JSON.stringify(reactShimUrl),
    )
    .replace(
      /from ["']\.\/chartPreferences["']/g,
      "from " + JSON.stringify(preferenceModuleUrl),
    );
  const { useChartPreferences } = await import(
    "data:text/javascript;base64," +
      Buffer.from(hookCompiled).toString("base64")
  );
  const firstPageChart = useChartPreferences();
  const secondPageChart = useChartPreferences();
  assert.equal(firstPageChart.update, secondPageChart.update);
  firstPageChart.update({ stockPanels: [], stockShowBoll: true });
  assert.deepEqual(useChartPreferences().preferences.stockPanels, []);
  assert.equal(useChartPreferences().preferences.stockShowBoll, true);
  const onStorage = listeners.get("storage");
  assert.equal(typeof onStorage, "function");
  onStorage({
    key: PREVIOUS_CHART_PREFERENCES_KEY,
    newValue: JSON.stringify({ version: 3, stockShowWaves: false }),
    storageArea: hookDisk.storage,
  });
  assert.deepEqual(useChartPreferences().preferences.stockPanels, []);
  assert.equal(useChartPreferences().preferences.stockShowBoll, true);
  onStorage({
    key: CHART_PREFERENCES_KEY,
    newValue: JSON.stringify({
      version: 4,
      stockPanels: ["macd"],
      stockShowBoll: false,
      indexPanels: [],
    }),
    storageArea: hookDisk.storage,
  });
  assert.deepEqual(useChartPreferences().preferences.stockPanels, ["macd"]);
  assert.deepEqual(useChartPreferences().preferences.indexPanels, []);
  assert.equal(useChartPreferences().preferences.stockShowBoll, false);
} finally {
  if (oldWindow) Object.defineProperty(globalThis, "window", oldWindow);
  else delete globalThis.window;
}
console.log(
  "chart preferences v4: v1/v2/v3 migration, preserved false and wave modes, independent panels/BOLL, empty selection, subscriptions, reload, old-key events and restricted storage passed",
);
