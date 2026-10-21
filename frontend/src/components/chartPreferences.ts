/** Non-sensitive chart display defaults only; no securities, accounts or keys. */
export const CHART_PREFERENCES_KEY = "shixu-chart-preferences-v1";
export const MA_WINDOWS = [5, 10, 20, 30, 60, 250] as const;
export type ChartPreferences = {
  version: 1;
  mas: number[];
  stockPriceBasis: "none" | "forward";
  showLimits: boolean;
  showEventBaseline: boolean;
};
export type PreferenceStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};
const defaults = (): ChartPreferences => ({
  version: 1,
  mas: [5, 10, 20, 30, 60],
  stockPriceBasis: "none",
  showLimits: true,
  showEventBaseline: true,
});

/** Validate the version and every field, and whitelist serialized properties. */
export function normalizeChartPreferences(value: unknown): ChartPreferences {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return defaults();
  const input = value as Record<string, unknown>;
  if (input.version !== 1) return defaults();
  const validMas =
    Array.isArray(input.mas) &&
    input.mas.length <= MA_WINDOWS.length &&
    input.mas.every(
      (value) =>
        typeof value === "number" &&
        MA_WINDOWS.includes(value as (typeof MA_WINDOWS)[number]),
    );
  return {
    version: 1,
    mas: validMas
      ? MA_WINDOWS.filter((value) => (input.mas as number[]).includes(value))
      : defaults().mas,
    stockPriceBasis: input.stockPriceBasis === "forward" ? "forward" : "none",
    showLimits: typeof input.showLimits === "boolean" ? input.showLimits : true,
    showEventBaseline:
      typeof input.showEventBaseline === "boolean"
        ? input.showEventBaseline
        : true,
  };
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
      storage?.getItem(CHART_PREFERENCES_KEY) ?? null,
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
        version: 1,
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
