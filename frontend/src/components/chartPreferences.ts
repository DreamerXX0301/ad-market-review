/** Non-sensitive chart display defaults only; no securities, accounts or keys. */
export const CHART_PREFERENCES_KEY = "shixu-chart-preferences-v2";
export const LEGACY_CHART_PREFERENCES_KEY = "shixu-chart-preferences-v1";
export const MA_WINDOWS = [5, 10, 20, 30, 60, 250] as const;
export type ChartPreferences = {
  version: 2;
  stockMas: number[];
  indexMas: number[];
  stockPriceBasis: "none" | "forward";
  showLimits: boolean;
  showEventBaseline: boolean;
};
export type PreferenceStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};
const defaults = (): ChartPreferences => ({
  version: 2,
  stockMas: [5, 10, 20, 30],
  indexMas: [5, 10, 20, 30, 60, 250],
  stockPriceBasis: "none",
  showLimits: true,
  showEventBaseline: true,
});

/** Validate the version and every field, and whitelist serialized properties. */
export function normalizeChartPreferences(value: unknown): ChartPreferences {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return defaults();
  const input = value as Record<string, unknown>;
  if (input.version !== 1 && input.version !== 2) return defaults();
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
  return {
    version: 2,
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
  };
}

/** Stock and index display choices never update each other's moving averages. */
export function effectiveChartMas(
  preferences: ChartPreferences,
  isIndex: boolean,
) {
  return [...(isIndex ? preferences.indexMas : preferences.stockMas)];
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
        version: 2,
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
  };
}
