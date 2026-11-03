/** Non-sensitive chart display defaults only; no securities, accounts or keys. */
import type { WaveMode } from "./wavePresentation";

export const CHART_PREFERENCES_KEY = "shixu-chart-preferences-v4";
export const PREVIOUS_CHART_PREFERENCES_KEY = "shixu-chart-preferences-v3";
export const EARLIER_CHART_PREFERENCES_KEY = "shixu-chart-preferences-v2";
export const LEGACY_CHART_PREFERENCES_KEY = "shixu-chart-preferences-v1";
export const MA_WINDOWS = [5, 10, 20, 30, 60, 250] as const;
export const CHART_INDICATOR_ORDER = [
  "volume",
  "boll",
  "kdj",
  "macd",
  "breadth",
] as const;
export const STOCK_CHART_PANELS = ["volume", "kdj", "macd"] as const;
export const INDEX_CHART_PANELS = [...STOCK_CHART_PANELS, "breadth"] as const;
export type ChartPanel = (typeof INDEX_CHART_PANELS)[number];
export type ChartPreferences = {
  version: 4;
  stockMas: number[];
  indexMas: number[];
  stockPriceBasis: "none" | "forward";
  showLimits: boolean;
  showEventBaseline: boolean;
  stockShowAutomaticTrendlines: boolean;
  indexShowAutomaticTrendlines: boolean;
  stockShowAllTrendHistory: boolean;
  indexShowAllTrendHistory: boolean;
  stockShowWaves: boolean;
  indexShowWaves: boolean;
  stockWaveMode: WaveMode;
  indexWaveMode: WaveMode;
  stockPanels: ChartPanel[];
  indexPanels: ChartPanel[];
  stockShowBoll: boolean;
  indexShowBoll: boolean;
};
export type PreferenceStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};
const defaults = (): ChartPreferences => ({
  version: 4,
  stockMas: [5, 10, 20, 30],
  indexMas: [5, 10, 20, 30, 60, 250],
  stockPriceBasis: "none",
  showLimits: true,
  showEventBaseline: true,
  stockShowAutomaticTrendlines: true,
  indexShowAutomaticTrendlines: true,
  stockShowAllTrendHistory: false,
  indexShowAllTrendHistory: false,
  stockShowWaves: true,
  indexShowWaves: true,
  stockWaveMode: "turning",
  indexWaveMode: "latest",
  stockPanels: [...STOCK_CHART_PANELS],
  indexPanels: [...INDEX_CHART_PANELS],
  stockShowBoll: false,
  indexShowBoll: false,
});

/** Validate the version and every field, and whitelist serialized properties. */
export function normalizeChartPreferences(value: unknown): ChartPreferences {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return defaults();
  const input = value as Record<string, unknown>;
  if (![1, 2, 3, 4].includes(input.version as number)) return defaults();
  const waveMode = (value: unknown, fallback: WaveMode): WaveMode =>
    value === "turning"
      ? "turning"
      : value === "latest" || value === "up" || value === "down"
        ? "latest"
        : fallback;
  const mas = (value: unknown, fallback: number[]) =>
    Array.isArray(value) &&
    value.length <= MA_WINDOWS.length &&
    value.every(
      (value) =>
        typeof value === "number" &&
        MA_WINDOWS.includes(value as (typeof MA_WINDOWS)[number]),
    )
      ? MA_WINDOWS.filter((window) => value.includes(window))
      : fallback;
  const panels = (value: unknown, allowed: readonly ChartPanel[]) =>
    Array.isArray(value) &&
    value.length <= allowed.length &&
    value.every((panel) => allowed.includes(panel))
      ? allowed.filter((panel) => value.includes(panel))
      : [...allowed];
  return {
    version: 4,
    stockMas:
      input.version === 1
        ? defaults().stockMas
        : mas(input.stockMas, defaults().stockMas),
    indexMas: mas(
      input.version === 1 ? input.mas : input.indexMas,
      defaults().indexMas,
    ),
    stockPriceBasis: input.stockPriceBasis === "forward" ? "forward" : "none",
    showLimits: typeof input.showLimits === "boolean" ? input.showLimits : true,
    showEventBaseline:
      typeof input.showEventBaseline === "boolean"
        ? input.showEventBaseline
        : true,
    stockShowAutomaticTrendlines:
      input.version !== 4 || input.stockShowAutomaticTrendlines !== false,
    indexShowAutomaticTrendlines:
      input.version !== 4 || input.indexShowAutomaticTrendlines !== false,
    stockShowAllTrendHistory:
      input.version === 4 && input.stockShowAllTrendHistory === true,
    indexShowAllTrendHistory:
      input.version === 4 && input.indexShowAllTrendHistory === true,
    stockShowWaves:
      (input.version === 3 || input.version === 4) &&
      typeof input.stockShowWaves === "boolean"
        ? input.stockShowWaves
        : true,
    indexShowWaves:
      (input.version === 3 || input.version === 4) &&
      typeof input.indexShowWaves === "boolean"
        ? input.indexShowWaves
        : true,
    stockWaveMode:
      input.version === 3 || input.version === 4
        ? waveMode(input.stockWaveMode, "turning")
        : "turning",
    indexWaveMode:
      input.version === 3 || input.version === 4
        ? waveMode(input.indexWaveMode, "latest")
        : "latest",
    stockPanels: panels(
      input.version === 4 ? input.stockPanels : undefined,
      STOCK_CHART_PANELS,
    ),
    indexPanels: panels(
      input.version === 4 ? input.indexPanels : undefined,
      INDEX_CHART_PANELS,
    ),
    stockShowBoll: input.version === 4 && input.stockShowBoll === true,
    indexShowBoll: input.version === 4 && input.indexShowBoll === true,
  };
}

/** Stock and index display choices never update each other's moving averages. */
export function effectiveChartMas(
  preferences: ChartPreferences,
  isIndex: boolean,
) {
  return [...(isIndex ? preferences.indexMas : preferences.stockMas)];
}

/** Wave visibility and range belong to the chart kind, never a selected date. */
export function effectiveChartWaves(
  preferences: ChartPreferences,
  isIndex: boolean,
) {
  return isIndex
    ? {
        showWaves: preferences.indexShowWaves,
        waveMode: preferences.indexWaveMode,
      }
    : {
        showWaves: preferences.stockShowWaves,
        waveMode: preferences.stockWaveMode,
      };
}

/** Empty selection is intentional; never add a panel from the other chart kind. */
export function effectiveChartPanels(
  preferences: ChartPreferences,
  isIndex: boolean,
): ChartPanel[] {
  return [...(isIndex ? preferences.indexPanels : preferences.stockPanels)];
}

/** BOLL has its own lower pane while retaining the existing visibility setting. */
export function effectiveChartBoll(
  preferences: ChartPreferences,
  isIndex: boolean,
): boolean {
  return isIndex ? preferences.indexShowBoll : preferences.stockShowBoll;
}

export function parseChartPreferences(raw: string | null): ChartPreferences {
  try {
    return normalizeChartPreferences(raw ? JSON.parse(raw) : null);
  } catch {
    return defaults();
  }
}

/** Index point series never inherit a stock's adjustment model. */
export function effectiveChartPriceBasis(
  preferences: ChartPreferences,
  isIndex: boolean,
) {
  return isIndex ? "none" : preferences.stockPriceBasis;
}

export function createChartPreferenceStore(storage?: PreferenceStorage | null) {
  let initial = defaults(),
    persistent = !!storage;
  try {
    initial = parseChartPreferences(
      storage?.getItem(CHART_PREFERENCES_KEY) ??
        storage?.getItem(PREVIOUS_CHART_PREFERENCES_KEY) ??
        storage?.getItem(EARLIER_CHART_PREFERENCES_KEY) ??
        storage?.getItem(LEGACY_CHART_PREFERENCES_KEY) ??
        null,
    );
  } catch {
    persistent = false;
  }
  let snapshot = { preferences: initial, persistent };
  const listeners = new Set<() => void>();
  function publish(preferences: ChartPreferences, nextPersistent: boolean) {
    if (
      JSON.stringify(snapshot.preferences) === JSON.stringify(preferences) &&
      snapshot.persistent === nextPersistent
    )
      return;
    snapshot = { preferences, persistent: nextPersistent };
    for (const listener of listeners) listener();
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update: (patch: Partial<Omit<ChartPreferences, "version">>) => {
      const preferences = normalizeChartPreferences({
        ...snapshot.preferences,
        ...patch,
        version: 4,
      });
      let saved = false;
      try {
        if (storage) {
          storage.setItem(CHART_PREFERENCES_KEY, JSON.stringify(preferences));
          saved = true;
        }
      } catch {
        /* Browser privacy/quota failures keep this window's live preferences. */
      }
      publish(preferences, saved);
    },
    reset: () => {
      const preferences = defaults();
      let saved = false;
      try {
        if (storage) {
          storage.setItem(CHART_PREFERENCES_KEY, JSON.stringify(preferences));
          saved = true;
        }
      } catch {
        /* Same in-memory fallback as direct toolbar changes. */
      }
      publish(preferences, saved);
    },
    receiveStorage: (raw: string | null) =>
      publish(parseChartPreferences(raw), !!storage),
    receiveStorageEvent: (event: {
      key: string | null;
      newValue: string | null;
      storageArea?: PreferenceStorage | null;
    }) => {
      if (
        (event.key === CHART_PREFERENCES_KEY || event.key === null) &&
        (!event.storageArea || event.storageArea === storage)
      )
        publish(
          parseChartPreferences(event.key === null ? null : event.newValue),
          !!storage,
        );
    },
  };
}
