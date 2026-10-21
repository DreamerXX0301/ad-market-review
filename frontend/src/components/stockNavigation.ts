/** A browsing snapshot follows the visible result's order before local pagination. */
import type { ChartEventBaseline } from "./chartDisplay";

export type StockNavigationItem = {
  id: string;
  name?: string;
  code?: string;
  eventBaseline?: ChartEventBaseline;
};
export type StockNavigation = {
  source: "strategy" | "tracking" | "watchlist";
  scopeKey: string;
  label: string;
  asOf?: string;
  items: StockNavigationItem[];
};
export type StockSelection = {
  id: string;
  asOf?: string;
  navigation?: StockNavigation;
};

type NavigationRow = {
  instrument_id?: unknown;
  name?: unknown;
  code?: unknown;
  eventBaseline?: ChartEventBaseline;
};

export function trackingEventBaseline(
  round: Record<string, any>,
): ChartEventBaseline {
  return {
    date: round.entry_business_date || "",
    rawClose:
      typeof round.entry_price === "number" ? round.entry_price : Number.NaN,
    priceBasis: round.entry_basis?.price_basis || "unknown",
    label: "事件日收盘",
    roundId: round.round_id,
  };
}

export function createStockNavigation(
  orderedRows: NavigationRow[],
  scope: Omit<StockNavigation, "source" | "items"> & {
    source?: StockNavigation["source"];
  },
  preferredRow?: NavigationRow,
): StockNavigation {
  const seen = new Set<string>();
  const items: StockNavigationItem[] = [];
  for (const row of orderedRows) {
    const id =
      typeof row.instrument_id === "string"
        ? row.instrument_id.trim().toUpperCase()
        : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const preferred =
      typeof preferredRow?.instrument_id === "string" &&
      preferredRow.instrument_id.trim().toUpperCase() === id
        ? preferredRow
        : row;
    items.push({
      id,
      name: typeof row.name === "string" ? row.name : undefined,
      code: typeof row.code === "string" ? row.code : undefined,
      ...((scope.source === "tracking" || scope.source === "watchlist") &&
      preferred.eventBaseline
        ? { eventBaseline: { ...preferred.eventBaseline } }
        : {}),
    });
  }
  return { ...scope, source: scope.source || "strategy", items };
}

export function stockNavigationPosition(
  navigation: StockNavigation | undefined,
  id: string,
) {
  if (
    !navigation ||
    !["strategy", "tracking", "watchlist"].includes(navigation.source)
  )
    return null;
  const index = navigation.items.findIndex(
    (item) => item.id === id.toUpperCase(),
  );
  if (index < 0) return null;
  return {
    index,
    position: index + 1,
    total: navigation.items.length,
    current: navigation.items[index],
    previousId: navigation.items[index - 1]?.id,
    nextId: navigation.items[index + 1]?.id,
  };
}

export function stockSelectionForOpen(
  id: string,
  navigation?: StockNavigation,
  fallbackAsOf?: string,
): StockSelection {
  const valid = stockNavigationPosition(navigation, id)
    ? navigation
    : undefined;
  return { id, navigation: valid, asOf: valid ? valid.asOf : fallbackAsOf };
}

export function stepStockSelection(
  current: StockSelection,
  direction: -1 | 1,
): StockSelection {
  const position = stockNavigationPosition(current.navigation, current.id);
  const id = direction === -1 ? position?.previousId : position?.nextId;
  if (!id) return current;
  // Build a new security selection: round-specific event baselines and other
  // data belonging to the previous stock must never follow it to the next one.
  return { id, asOf: current.navigation?.asOf, navigation: current.navigation };
}

export function stockPayloadMatches(
  payload: unknown,
  id: string,
  asOf?: string,
): boolean {
  if (!payload || typeof payload !== "object") return false;
  const data = payload as Record<string, any>;
  const instrument = data.instrument || data;
  const actual = instrument.instrument_id || instrument.thscode;
  if (typeof actual !== "string" || actual.toUpperCase() !== id.toUpperCase())
    return false;
  const observed = data.as_of || data.data_date;
  return !asOf || !observed || observed === asOf;
}
